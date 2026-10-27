import { randomBytes } from 'node:crypto';
import { isAbsolute, join } from 'node:path';

import type { LoopHook, LoopHookPoint as LoopHookPointName } from '../hooks/loop.ts';
import type { GitCli } from './git-cli.ts';

import { getLogger } from '../diagnostics.ts';
import { LoopHookPoint } from '../hooks/loop.ts';
import { addNote } from '../notes.ts';
import { ValueError } from '../value-error.ts';
import { WorktreeError } from './errors.ts';
import { isInside, resolvePath } from './paths.ts';

const logger = getLogger('loop.git.strategies');

/** Owns one git lifecycle around a run and runs `body` in the directory the agent works in. */
export interface GitStrategy {
  open: <T>(
    git: GitCli,
    cwd: string,
    repository: string,
    options: WorktreeSettings,
    body: (path: string) => Promise<T>,
  ) => Promise<T>;
}

/** What a strategy reads from the git options. */
export interface WorktreeSettings {
  readonly hooksAt: (point: LoopHookPointName) => readonly LoopHook[];
  readonly rootPath: string;
}

/** Reuse a local branch; a new branch starts at explicit base, else origin branch or HEAD. */
export class BranchStrategy implements GitStrategy {
  readonly baseBranch: null | string;
  readonly branch: string;

  constructor(branch: string, baseBranch: null | string = null) {
    this.branch = branch;
    this.baseBranch = baseBranch;
    Object.freeze(this);
  }

  async open<T>(
    git: GitCli,
    cwd: string,
    repository: string,
    options: WorktreeSettings,
    body: (path: string) => Promise<T>,
  ): Promise<T> {
    await git.fetch(repository);
    return worktreeScope(git, cwd, repository, options, this.branch, this.baseBranch, body);
  }
}

/** No worktree or branch: the agent works directly in the repository directory. */
export class HeadStrategy implements GitStrategy {
  constructor() {
    Object.freeze(this);
  }

  open<T>(
    _git: GitCli,
    _cwd: string,
    repository: string,
    _options: WorktreeSettings,
    body: (path: string) => Promise<T>,
  ): Promise<T> {
    return body(repository);
  }
}

/** Temp-branch worktree, fast-forward merged into the current HEAD on success, then the temp branch is deleted. */
export class MergeToHeadStrategy implements GitStrategy {
  constructor() {
    Object.freeze(this);
  }

  async open<T>(
    git: GitCli,
    cwd: string,
    repository: string,
    options: WorktreeSettings,
    body: (path: string) => Promise<T>,
  ): Promise<T> {
    const branch = `tmp_${randomBytes(4).toString('hex')}`;
    const result = await worktreeScope(git, cwd, repository, options, branch, 'HEAD', body);
    // Reached only when the run succeeded; on failure the temp branch is kept unmerged.
    await git.mergeFfOnly(repository, branch);
    await git.deleteBranch(repository, branch);
    return result;
  }
}

async function cleanup(git: GitCli, repository: string, target: string): Promise<boolean> {
  if (!(await git.isClean(target))) {
    logger.warning(`dirty worktree retained: ${target}`);
    return false;
  }
  await git.removeWorktree(repository, target);
  return true;
}

/**
 * Creates or reuses the worktree, runs the hooks around `body`, and leaves a clean worktree removed and a dirty one kept
 * on every exit path. Removal hooks run only after a successful body.
 */
async function worktreeScope<T>(
  git: GitCli,
  cwd: string,
  repository: string,
  options: WorktreeSettings,
  branch: string,
  base: null | string,
  body: (path: string) => Promise<T>,
): Promise<T> {
  const root = await resolvePath(isAbsolute(options.rootPath) ? options.rootPath : join(cwd, options.rootPath));
  if (!isAbsolute(options.rootPath) && !isInside(await resolvePath(cwd), root)) {
    throw new ValueError(`worktrees root ${root} must be inside ${cwd}`);
  }
  const target = join(root, branch);
  if (!isInside(root, await resolvePath(target))) {
    throw new WorktreeError(`Worktree path escapes its root: ${target}`);
  }
  // Validate before filesystem mutation; Git also rejects unsafe branch names.
  await git.addWorktree(repository, target, branch, base);
  let result: T;
  try {
    for (const hook of options.hooksAt(LoopHookPoint.WORKTREE_READY)) {
      await git.runHook(hook, target, repository, target);
    }
    result = await body(target);
    for (const hook of options.hooksAt(LoopHookPoint.WORKTREE_REMOVING)) {
      await git.runHook(hook, target, repository, target);
    }
  } catch (error) {
    try {
      await cleanup(git, repository, target);
    } catch (cleanupError) {
      const reason = cleanupError instanceof Error ? cleanupError.message : String(cleanupError);
      const note = `worktree cleanup also failed; retained ${target}: ${reason}`;
      logger.error(note);
      addNote(error, note);
    }
    throw error;
  }
  if (!(await cleanup(git, repository, target))) {
    throw new WorktreeError(`Worktree has uncommitted changes; preserved at ${target}`);
  }
  return result;
}
