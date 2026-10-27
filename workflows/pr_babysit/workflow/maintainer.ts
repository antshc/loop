import type { Snapshot } from '../infrastructure/github.ts';
import type { Maintenance } from './maintenance.ts';
import type { Batch } from './records.ts';

import { MAINTAINER } from '../agents/profiles.ts';
import { digest } from '../infrastructure/github.ts';
import { Blocked } from '../infrastructure/state.ts';
import { ask } from './address.ts';
import { agentHead, commitIssue, publish, readOnly, recoverCommit } from './changes.ts';
import { batchOf, recordOf, requireBatch } from './records.ts';
import { ciState } from './watcher.ts';
import { openWorkspace } from './workspace.ts';

/** Diagnose failures before choosing Workflow-owned publication or bounded retries. */
export async function maintainCi(context: Maintenance, snapshot: Snapshot): Promise<void> {
  const { flow, git, github, repository, settings, state } = context;
  const maintainer = MAINTAINER.with({ timeoutSec: settings.timeout });
  const ci = ciState(snapshot);
  const retrySignal = digest(ci.runs.map((run) => [run.id, run.run_attempt ?? 1]));
  if (batchOf(state) === null && recordOf(state.data.retry_wait)[snapshot.pr.head.sha] === retrySignal) return;
  await openWorkspace(context, snapshot, async (workspace) => {
    const path = workspace.path;
    if (batchOf(state) !== null) {
      await recoverCommit(state, git, path);
      if (requireBatch(state).index > 0) {
        await publish(context, path, repository.name, snapshot.pr.number);
        return;
      }
    }
    if (!(await git.clean(path))) throw new Blocked('Worktree contains unverified edits');
    const head = await git.head(path);
    const logs =
      ci.runs.length > 0
        ? await github.logs(repository.name, ci.runs)
        : 'No Actions run logs; inspect check/status links.';
    context.checkpoint();
    const diagnosis = await flow.phase('diagnose', () => ask(workspace, maintainer, 'diagnose', { logs, snapshot: snapshot }));
    await readOnly(git, path, head);
    const classification = diagnosis.classification;
    if (classification === 'transient') {
      if (ci.pending) return;
      const used = Number(recordOf(state.data.retries)[head] ?? 0);
      if (used >= settings.retries || ci.runs.length === 0) {
        throw new Blocked('Transient CI retry budget exhausted or no rerunnable runs');
      }
      // Reserve the cycle before side effects, so a crash cannot exceed the budget.
      state.data.retries[head] = used + 1;
      const retryWait = recordOf(state.data.retry_wait);
      retryWait[head] = retrySignal;
      state.data.retry_wait = retryWait;
      state.save();
      await flow.phase('publish', { attributes: { action: 'retry' } }, async () => {
        for (const run of ci.runs) await github.retry(repository.name, run.id);
      });
      return;
    }
    if (classification !== 'branch') {
      throw new Blocked(reasonOf(diagnosis.reason) ?? 'CI diagnosis is unclear');
    }
    if (batchOf(state) === null) {
      const batch: Batch = {
        base: head,
        branch: snapshot.pr.head.ref,
        head,
        index: 0,
        inflight: null,
        kind: 'ci',
        replies: {},
      };
      state.data.batch = batch;
      state.save();
    }
    context.checkpoint();
    const result = await flow.phase('fix-ci', () => ask(workspace, maintainer, 'fix', { diagnosis, snapshot: snapshot }));
    await agentHead(git, path, head);
    if (result.verdict !== 'fixed') throw new Blocked(reasonOf(result.reason) ?? 'Maintainer could not fix CI');
    await commitIssue(context, path, `babysit: fix CI (PR #${String(snapshot.pr.number)})`, {});
    await publish(context, path, repository.name, snapshot.pr.number);
  });
}

/** Python's `x.get("reason") or fallback`: only a non-empty string counts. */
function reasonOf(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}
