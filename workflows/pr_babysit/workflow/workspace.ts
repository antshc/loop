import { join } from 'node:path';

import type { Snapshot } from '../infrastructure/github.ts';
import type { Maintenance } from './maintenance.ts';

import { type AgentWorkspace, BranchStrategy, GitOptions } from '../../../src/loop/index.ts';
import { digest } from '../infrastructure/github.ts';
import { Blocked, processLock } from '../infrastructure/state.ts';
import { batchOf } from './records.ts';

/** Serialize a named branch and compose Loop's built-in recoverable worktree lifecycle around `body`. */
export async function openWorkspace<T>(
  context: Maintenance,
  snapshot: Snapshot,
  body: (workspace: AgentWorkspace) => Promise<T>,
): Promise<T> {
  const { git, newAgent, observer, repository, settings, state } = context;
  state.data.worktree_branch ??= `babysit/${repository.name}/${String(snapshot.pr.number)}`;
  const branch = state.data.worktree_branch as string;
  const root = join(settings.stateDir, 'worktrees');
  const lock = join(settings.stateDir, 'locks', `${digest([repository.path, branch])}.lock`);
  return processLock(lock, async () => {
    const batch = batchOf(state);
    // A missing worktree is normal after a clean failure; its branch retains the commits.
    const start = batch === null ? snapshot.pr.head.sha : batch.head;
    await git.fetchRevision(repository.path, snapshot.pr.head.sha);
    const builder = newAgent();
    if (observer !== null) builder.withObserver(observer);
    const options = new GitOptions({
      repositoryPath: repository.path,
      rootPath: root,
      strategy: new BranchStrategy(branch, start),
    });
    return builder.withGit(options).open(async (workspace) => {
      if (batch === null) {
        if (!(await git.clean(workspace.path))) throw new Blocked('Unverified edits retained in worktree');
        const actual = await git.head(workspace.path);
        const expected = (state.data.worktree_base as string | undefined) ?? start;
        if (actual !== start) {
          if (actual !== expected) throw new Blocked('Unexpected local commits; inspect recovery branch');
          await git.advance(workspace.path, start);
        }
        state.data.worktree_base = start;
      }
      state.save();
      return body(workspace);
    });
  });
}
