import type { GitService } from '../../checkout.ts';

import { Commit } from '../../records.ts';

/** An in-memory `GitService` for the review and finalize phases: a commit list, a working-tree status and the origin refs. */
export class FakeReviewGit implements GitService {
  branchCommits = unused<Commit[]>('branchCommits');
  changedFiles = unused<string[]>('changedFiles');
  commit = unused<Commit>('commit');
  readonly commits: Commit[] = [new Commit('c0000001', 'ccode(CHK-1|10): Add basket')];

  commitsSince = unused<Commit[]>('commitsSince');

  readonly fetched: string[] = [];

  initiativeCommits = unused<Commit[]>('initiativeCommits');

  isClean = unused<boolean>('isClean');

  pushIfAhead = unused<boolean>('pushIfAhead');

  readonly refs = new Map<string, string>();

  remoteBranchExists = unused<boolean>('remoteBranchExists');

  restore = unused<undefined>('restore');

  #status = '';

  /** Moves HEAD the way an agent that committed would. */
  addCommit(subject: string): Commit {
    const commit = new Commit(`c${String(this.commits.length + 1).padStart(7, '0')}`, subject);
    this.commits.push(commit);
    return commit;
  }

  /** Dirties the working tree the way an agent that edited a file would. */
  edit(path: string): void {
    this.#status += ` M ${path}\n`;
  }

  fetch(path: string): Promise<void> {
    this.fetched.push(path);
    return Promise.resolve();
  }

  head(): Promise<Commit> {
    return Promise.resolve(this.#head());
  }

  refSha(_path: string, ref: string): Promise<string> {
    return Promise.resolve(this.refs.get(ref) ?? this.#head().sha);
  }

  status(): Promise<string> {
    return Promise.resolve(this.#status);
  }

  #head(): Commit {
    const head = this.commits.at(-1);
    if (head === undefined) {
      throw new Error('no commits');
    }
    return head;
  }
}

function unused<T>(name: string): () => Promise<T> {
  return () => Promise.reject(new Error(`${name} is not used by the review phases`));
}
