import type { Snapshot } from '../infrastructure/github.ts';
import type { Json, JsonObject } from '../infrastructure/json.ts';
import type { Maintenance } from './maintenance.ts';
import type { AddressBatch, FeedbackItem, Issue } from './records.ts';

import { type AgentProfile, AgentRequest, type AgentWorkspace } from '../../../src/loop/index.ts';
import { ProcessFailedError } from '../../../src/loop/index.ts';
import { ADDRESS } from '../agents/profiles.ts';
import { compareCodePoints } from '../infrastructure/json.ts';
import { Blocked } from '../infrastructure/state.ts';
import { agentHead, commitIssue, publish, readOnly, recoverCommit } from './changes.ts';
import { itemOf } from './records.ts';
import { openWorkspace } from './workspace.ts';

const ISSUE_ID = /^[a-zA-Z0-9_-]{1,80}$/;
const VERDICTS: readonly unknown[] = ['fix-needed', 'no-change-needed', 'unclear'];

/** Review edits must wait for the maintainer to repair the baseline. */
export class BaselineFailed extends Blocked {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'BaselineFailed';
  }
}

/** Resolve issues sequentially, with the Workflow verifying and publishing each batch. */
export async function addressReviews(
  context: Maintenance,
  snapshot: Snapshot,
  items: readonly FeedbackItem[],
): Promise<void> {
  const { flow, git, repository, settings, state, verify } = context;
  const address = ADDRESS.with({ timeoutSec: settings.timeout });
  await openWorkspace(context, snapshot, async (workspace) => {
    const path = workspace.path;
    if (state.data.batch === null) {
      if (!(await git.clean(path))) throw new Blocked('Worktree contains unrelated edits');
      await flow.phase('prepare', async () => {
        try {
          await verify(path, repository, settings.timeout);
        } catch (error) {
          if (error instanceof ProcessFailedError) throw new BaselineFailed('Baseline verification failed', { cause: error });
          throw error;
        }
      });
      const head = await git.head(path);
      context.checkpoint();
      const issues = await flow.phase('address', { attributes: { mode: 'group' } }, () =>
        groupIssues(workspace, address, items),
      );
      await readOnly(git, path, head);
      const batch: AddressBatch = {
        base: head,
        branch: snapshot.pr.head.ref,
        head,
        index: 0,
        inflight: null,
        issues,
        kind: 'address',
        pushed: false,
        replies: {},
        unclear: false,
      };
      state.data.batch = batch;
      state.save();
    }
    await recoverCommit(state, git, path);
    const batch = state.data.batch as AddressBatch;
    while (batch.index < batch.issues.length) {
      const issue = batch.issues[batch.index];
      if (issue === undefined) break;
      const head = await git.head(path);
      context.checkpoint();
      const result = await flow.phase('address', { attributes: { issue: issue.id } }, async () => {
        const resolution = await ask(workspace, address, 'resolve', {
          issue,
          items: issue.keys.map((key) => itemOf(state, key)),
        });
        validateResolution(resolution, issue.keys);
        return resolution;
      });
      await agentHead(git, path, head);
      const replies = result.replies as Record<string, string>;
      if (result.verdict === 'fix-needed') {
        await commitIssue(context, path, `babysit: address ${issue.id} (PR #${String(snapshot.pr.number)})`, replies);
      } else {
        await readOnly(git, path, head);
        Object.assign(batch.replies, replies);
        batch.unclear ||= result.verdict === 'unclear';
        batch.index += 1;
        state.save();
      }
    }
    const unclear = batch.unclear;
    await publish(context, path, repository.name, snapshot.pr.number);
    if (unclear) throw new Blocked('Reviewer clarification requested');
  });
}

/** Run one agent request and validate its JSON envelope. */
export async function ask(
  workspace: AgentWorkspace,
  profile: AgentProfile,
  mode: string,
  payload: Readonly<Record<string, unknown>>,
): Promise<JsonObject> {
  const name = profile.name ?? '';
  const result = await workspace.agent(profile).run(new AgentRequest(JSON.stringify({ mode, ...payload })));
  if (result.exitCode !== 0) throw new Blocked(`${name} exited ${String(result.exitCode)}`);
  let value: unknown;
  try {
    value = JSON.parse(result.output);
  } catch (error) {
    throw new Blocked(`${name} returned invalid JSON`, { cause: error });
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Blocked(`${name} must return an object`);
  return value as JsonObject;
}

/** Require complete and unique feedback coverage before editing. */
export async function groupIssues(
  workspace: AgentWorkspace,
  profile: AgentProfile,
  items: readonly FeedbackItem[],
): Promise<Issue[]> {
  const issues = (await ask(workspace, profile, 'group', { items })).issues;
  if (!Array.isArray(issues) || issues.length === 0) throw new Blocked('Address returned no issues');
  const keys: string[] = [];
  const ids: string[] = [];
  for (const issue of issues) {
    if (!isIssue(issue)) throw new Blocked('Address returned an invalid issue');
    keys.push(...issue.keys);
    ids.push(issue.id);
  }
  const expected = items.map((item) => item.key).sort(compareCodePoints);
  if (ids.length !== new Set(ids).size || keys.sort(compareCodePoints).join('\0') !== expected.join('\0')) {
    throw new Blocked('Address must assign every feedback item exactly once');
  }
  return issues as Issue[];
}

/** Require justified verdicts and one usable reply per feedback item. */
export function validateResolution(result: JsonObject, keys: readonly string[]): void {
  if (!VERDICTS.includes(result.verdict)) throw new Blocked('Invalid Address verdict');
  const replies = result.replies;
  if (
    typeof replies !== 'object' ||
    replies === null ||
    Array.isArray(replies) ||
    !sameKeys(Object.keys(replies), keys) ||
    Object.values(replies).some((body) => typeof body !== 'string' || body.trim() === '')
  ) {
    throw new Blocked('Address must draft one reply for every feedback item');
  }
  const evidence: Json = result.evidence ?? [];
  if (
    result.verdict !== 'unclear' &&
    !(Array.isArray(evidence) && evidence.some((citation) => typeof citation === 'string' && /\S+:\d+/.test(citation)))
  ) {
    throw new Blocked('Address verdict requires path:line evidence');
  }
}

function isIssue(issue: unknown): issue is Issue {
  if (typeof issue !== 'object' || issue === null || Array.isArray(issue)) return false;
  const { id, keys, statement } = issue as Record<string, unknown>;
  return (
    typeof id === 'string' &&
    ISSUE_ID.test(id) &&
    typeof statement === 'string' &&
    Array.isArray(keys) &&
    keys.length > 0 &&
    keys.every((key) => typeof key === 'string')
  );
}

function sameKeys(actual: readonly string[], expected: readonly string[]): boolean {
  const wanted = new Set(expected);
  return actual.length === wanted.size && actual.every((key) => wanted.has(key));
}
