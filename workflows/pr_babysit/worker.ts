import { inject, injectable } from 'inversify';

import type { AgentBuilder } from '../../src/loop/index.ts';
import type { WorkflowGit } from './infrastructure/git.ts';
import type { GithubService } from './infrastructure/github.ts';
import type { Verify } from './infrastructure/verification.ts';
import type { Settings } from './settings.ts';

import { type ProcessRunner } from '../../src/loop/index.ts';
import { StopRequest } from './infrastructure/signals.ts';
import { TOKENS } from './tokens.ts';
import { run } from './workflow/run.ts';

/** Maintain one explicit PR; resolves to the process exit code. */
export interface WorkerService {
  run: (settings: Settings, repo: string, number: number) => Promise<number>;
}

/** Maintain one explicit PR; an interrupt records `interrupted` and surfaces as `Cancelled` (exit 130 or 143). */
@injectable()
export class PrWorker implements WorkerService {
  readonly #fileLockGuard: () => void;
  readonly #git: WorkflowGit;
  readonly #github: GithubService;
  readonly #newAgent: () => AgentBuilder;
  readonly #stop: StopRequest;
  readonly #verification: Verify;

  constructor(
    @inject(TOKENS.FileLockGuard) fileLockGuard: () => void,
    @inject(TOKENS.WorkflowGit) git: WorkflowGit,
    @inject(TOKENS.GithubService) github: GithubService,
    @inject(TOKENS.AgentFactory) newAgent: () => AgentBuilder,
    @inject(TOKENS.StopRequest) stop: StopRequest,
    @inject(TOKENS.Verification) verification: Verify,
  ) {
    this.#fileLockGuard = fileLockGuard;
    this.#git = git;
    this.#github = github;
    this.#newAgent = newAgent;
    this.#stop = stop;
    this.#verification = verification;
  }

  async run(settings: Settings, repo: string, number: number): Promise<number> {
    try {
      this.#fileLockGuard();
      return await run(settings, repo, number, {
        checkpoint: () => {
          this.#stop.throwIfRequested();
        },
        git: this.#git,
        github: this.#github,
        newAgent: this.#newAgent,
        verification: this.#verification,
        wait: async (seconds) => {
          await this.#stop.sleep(seconds);
          this.#stop.throwIfRequested();
        },
      });
    } finally {
      this.#stop.dispose();
    }
  }
}

/** A runner that starts nothing once the operator asked to stop, so a signal between children is never lost. */
export function refusingAfter(stop: StopRequest, runner: ProcessRunner): ProcessRunner {
  return {
    run: (argv, options) => {
      try {
        stop.throwIfRequested();
      } catch (error) {
        return Promise.reject(error instanceof Error ? error : new Error(String(error)));
      }
      return runner.run(argv, options);
    },
    start: (argv, options) => {
      stop.throwIfRequested();
      return runner.start(argv, options);
    },
  };
}
