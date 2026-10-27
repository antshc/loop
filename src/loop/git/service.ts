import { ValueError } from '../value-error.ts';

export interface GitOptionsInit {
  /** Repository that git -C targets (multi-repository setups); null uses the agent cwd. */
  repositoryPath?: null | string;
  /** Relative roots must stay inside cwd; an absolute root is an explicit location. */
  rootPath?: string;
}

/** The injectable seam that opens the directory the agent works in, and cleans it up after `body`. */
export interface GitService {
  open: <T>(cwd: string, options: GitOptions, body: (path: string) => Promise<T>) => Promise<T>;
}

/** Where the worktree lives (target = rootPath/branch). */
export class GitOptions {
  readonly repositoryPath: null | string;
  readonly rootPath: string;

  constructor(init: GitOptionsInit = {}) {
    if (init.rootPath === '') {
      throw new ValueError('rootPath must not be empty');
    }
    this.rootPath = init.rootPath ?? '.worktrees';
    this.repositoryPath = init.repositoryPath ?? null;
    Object.freeze(this);
  }
}
