import type { RunObserver, WorktreePoint } from '../observe/values.ts';
import type { GitOptions, GitService } from './service.ts';

import { getLogger } from '../diagnostics.ts';
import { WorktreeEvent } from '../observe/values.ts';
import { describeError } from '../text.ts';
import { pathExists } from './paths.ts';

const logger = getLogger('loop.git.observing');

/** Decorates a GitService: reports readiness, release attempts, and actual removal or retention. */
export class ObservingGitService implements GitService {
  readonly #clock: () => Date;
  readonly #inner: GitService;
  readonly #observer: RunObserver;

  constructor(inner: GitService, observer: RunObserver, clock: () => Date = () => new Date()) {
    this.#inner = inner;
    this.#observer = observer;
    this.#clock = clock;
  }

  async open<T>(cwd: string, options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    let path: null | string = null;
    let bodyOk = false;
    let removedOk = false;
    try {
      const result = await this.#inner.open(cwd, options, async (opened) => {
        path = opened;
        await this.#emit('ready', opened, null);
        try {
          const value = await body(opened);
          bodyOk = true;
          return value;
        } finally {
          await this.#emit('removing', opened, bodyOk);
        }
      });
      // Reached only when the body and the inner removal both succeeded.
      removedOk = bodyOk;
      return result;
    } finally {
      // `path` is assigned inside the callback, which the analysis cannot see.
      const opened = path as null | string;
      if (opened !== null) {
        await this.#emit((await pathExists(opened)) ? 'retained' : 'removed', opened, removedOk);
      }
    }
  }

  async #emit(point: WorktreePoint, path: string, ok: boolean | null): Promise<void> {
    try {
      await this.#observer.worktree(new WorktreeEvent({ at: this.#clock(), ok, path, point }));
    } catch (error) {
      // An observer is caller code; it must never fail the run.
      logger.warning(`observer failed on worktree ${point}: ${describeError(error)}`);
    }
  }
}
