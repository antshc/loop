import type { WorkflowGit } from '../infrastructure/git.ts';
import type { Snapshot } from '../infrastructure/github.ts';
import type { PrState } from '../infrastructure/state.ts';
import type { Maintenance } from './maintenance.ts';
import type { FeedbackItem } from './records.ts';

import { Blocked } from '../infrastructure/state.ts';
import { itemOf, requireBatch } from './records.ts';

export async function agentHead(git: WorkflowGit, path: string, before: string): Promise<void> {
  if ((await git.head(path)) !== before) throw new Blocked('Agent changed HEAD; Python must own commits');
}

/** Verify, then commit one issue's edits and record the new head; an interrupted commit is recoverable. */
export async function commitIssue(
  context: Maintenance,
  path: string,
  message: string,
  replies: Readonly<Record<string, string>>,
): Promise<void> {
  const { flow, git, repository, settings, state, verify } = context;
  const batch = requireBatch(state);
  if (await git.clean(path)) throw new Blocked('Agent claimed a fix but produced no changes');
  context.checkpoint();
  await flow.phase('verify', () => verify(path, repository, settings.timeout));
  batch.inflight = { message, parent: await git.head(path) };
  Object.assign(batch.replies, replies);
  state.save();
  batch.head = await git.commit(path, message);
  batch.index += 1;
  batch.inflight = null;
  state.save();
}

/** Push the batch head without force, confirm it, then post each reply exactly once and clear the batch. */
export async function publish(
  context: Pick<Maintenance, 'flow' | 'git' | 'github' | 'login' | 'state'>,
  path: string,
  repo: string,
  number: number,
): Promise<void> {
  const { flow, git, github, login, state } = context;
  const batch = requireBatch(state);
  await flow.phase('publish', { attributes: { head: batch.head } }, async () => {
    const current = await github.snapshot(repo, number);
    if (current.pr.state !== 'open') throw new Blocked('PR closed during publication; inspect retained work before retrying');
    if (current.pr.head.ref !== batch.branch) throw new Blocked('PR branch changed during publication');
    const remoteHead = current.pr.head.sha;
    if (remoteHead !== batch.base && remoteHead !== batch.head) {
      throw new Blocked('PR head moved; publication stopped before replying');
    }
    if (batch.head !== batch.base && remoteHead !== batch.head) await git.publish(path, batch.branch);
    const confirmed = await github.pullRequest(repo, number);
    if (confirmed.head.sha !== batch.head) throw new Blocked('Published SHA could not be confirmed');
    batch.pushed = true;
    state.save();
    for (const [key, reply] of Object.entries(batch.replies)) {
      const item = itemOf(state, key);
      if (item.status === 'handled') continue;
      const body = item.kind === 'thread' ? reply : `In response to ${item.url as string}:\n\n${reply}`;
      const latest = await github.snapshot(repo, number);
      if (item.kind === 'thread' && !replyExists(latest, item, body, login)) {
        const thread = latest.threads.find((candidate) => candidate.id === item.thread_id);
        const source = item.key.slice(item.key.lastIndexOf(':') + 1);
        if (thread !== undefined && thread.comments.length > 0 && thread.comments.at(-1)?.id !== source) {
          item.status = 'superseded';
          state.save();
          continue;
        }
      }
      if (!replyExists(latest, item, body, login)) await github.reply(repo, number, item, body);
      item.status = 'handled';
      state.save();
    }
    state.data.worktree_base = batch.head;
    state.data.batch = null;
    state.save();
  });
}

export async function readOnly(git: WorkflowGit, path: string, before: string): Promise<void> {
  await agentHead(git, path, before);
  if (!(await git.clean(path))) throw new Blocked('Read-only agent modified the worktree');
}

/** Resume an interrupted commit or reject a worktree that does not match the checkpoint. */
export async function recoverCommit(state: PrState, git: WorkflowGit, path: string): Promise<void> {
  const batch = requireBatch(state);
  const inflight = batch.inflight;
  if (inflight !== null) {
    const head = await git.head(path);
    if (head === inflight.parent) {
      throw new Blocked('Interrupted verification/commit; inspect retained worktree before retrying');
    }
    if (
      (await git.text(path, 'rev-parse', 'HEAD^')) !== inflight.parent ||
      (await git.text(path, 'log', '-1', '--format=%s')) !== inflight.message ||
      !(await git.clean(path))
    ) {
      throw new Blocked('Unrecognized commit in recovery worktree');
    }
    batch.head = head;
    batch.index += 1;
    batch.inflight = null;
    state.save();
  }
  if (!(await git.clean(path)) || (await git.head(path)) !== batch.head) {
    throw new Blocked('Unverified edits or unexpected commits retained in recovery worktree');
  }
}

function replyExists(snapshot: Snapshot, item: FeedbackItem, body: string, login: string): boolean {
  if (item.kind === 'thread') {
    const thread = snapshot.threads.find((candidate) => candidate.id === item.thread_id);
    if (thread === undefined || thread.isResolved) return true;
    const source = item.key.slice(item.key.lastIndexOf(':') + 1);
    const found = thread.comments.findIndex((comment) => comment.id === source);
    const index = found < 0 ? thread.comments.length : found;
    return thread.comments.slice(index + 1).some((comment) => comment.author?.login === login && comment.body === body);
  }
  return snapshot.other.some((comment) => comment.author?.login === login && comment.body === body);
}
