import type { LoopHook, LoopHookPoint } from '../hooks/loop.ts';
import type { GitStrategy } from './strategies.ts';

import { ValueError } from '../value-error.ts';
import { HeadStrategy } from './strategies.ts';

export interface GitOptionsInit {
  /** Loop hooks of any point; each runs at its own point, in declared order. */
  loopHooks?: readonly LoopHook[];
  /** Repository that git -C targets (multi-repository setups); null uses the agent cwd. */
  repositoryPath?: null | string;
  /** Relative roots must stay inside cwd; an absolute root is an explicit location. */
  rootPath?: string;
  /** Creates the worktree; defaults to none: the agent works in the repository directory. */
  strategy?: GitStrategy;
}

/** The injectable seam that opens the directory the agent works in, and cleans it up after `body`. */
export interface GitService {
  open: <T>(cwd: string, options: GitOptions, body: (path: string) => Promise<T>) => Promise<T>;
}

/** Where the worktree lives (target = rootPath/branch) and which strategy creates it. */
export class GitOptions {
  readonly loopHooks: readonly LoopHook[];
  readonly repositoryPath: null | string;
  readonly rootPath: string;
  readonly strategy: GitStrategy;

  constructor(init: GitOptionsInit = {}) {
    if (init.rootPath === '') {
      throw new ValueError('rootPath must not be empty');
    }
    this.rootPath = init.rootPath ?? '.worktrees';
    this.repositoryPath = init.repositoryPath ?? null;
    this.strategy = init.strategy ?? new HeadStrategy();
    this.loopHooks = Object.freeze([...(init.loopHooks ?? [])]);
    if (this.loopHooks.length > 0 && this.strategy instanceof HeadStrategy) {
      throw new ValueError('Loop hooks need a worktree strategy');
    }
    Object.freeze(this);
  }

  hooksAt(point: LoopHookPoint): readonly LoopHook[] {
    return this.loopHooks.filter((hook) => hook.point === point);
  }
}
