import { inject, injectable } from 'inversify';

import type { GithubService } from './infrastructure/github.ts';
import type { Settings } from './settings.ts';

import { type ProcessRunner } from '../../src/loop/index.ts';
import { serve } from './infrastructure/scheduler.ts';
import { StopRequest } from './infrastructure/signals.ts';
import { TOKENS } from './tokens.ts';

/** Discover PRs continuously; resolves to the process exit code. */
export interface DiscoveryService {
  serve: (settings: Settings, config: string, options: ServeOptions) => Promise<number>;
}

export interface ServeOptions {
  readonly includeExisting: boolean;
  readonly retry: string | undefined;
}

/** Discover PRs until interrupted, then stop the workers: they get SIGTERM and 10 s before SIGKILL. */
@injectable()
export class PrDiscovery implements DiscoveryService {
  readonly #fileLockGuard: () => void;
  readonly #github: GithubService;
  readonly #runner: ProcessRunner;
  readonly #stop: StopRequest;

  constructor(
    @inject(TOKENS.FileLockGuard) fileLockGuard: () => void,
    @inject(TOKENS.GithubService) github: GithubService,
    @inject(TOKENS.ProcessRunner) runner: ProcessRunner,
    @inject(TOKENS.StopRequest) stop: StopRequest,
  ) {
    this.#fileLockGuard = fileLockGuard;
    this.#github = github;
    this.#runner = runner;
    this.#stop = stop;
  }

  async serve(settings: Settings, config: string, options: ServeOptions): Promise<number> {
    try {
      this.#fileLockGuard();
      return await serve(settings, config, options, {
        github: this.#github,
        spawn: (argv, spawnOptions) => this.#runner.start(argv, spawnOptions),
        stop: () => this.#stop.requested,
        wait: (seconds) => this.#stop.sleep(seconds),
      });
    } finally {
      this.#stop.dispose();
    }
  }
}
