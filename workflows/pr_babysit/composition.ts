import type { ServeOptions } from './infrastructure/scheduler.ts';
import type { Settings } from './settings.ts';

import {
  createProcessRunner,
  processRunner,
  type ProcessRunner,
  ProcessSupervisor,
} from '../../src/loop/index.ts';
import { assertFileLockAvailable } from './file-lock.ts';
import { Git } from './infrastructure/git.ts';
import { Github } from './infrastructure/github.ts';
import { serve } from './infrastructure/scheduler.ts';
import { StopRequest } from './infrastructure/signals.ts';
import { createVerification } from './infrastructure/verification.ts';
import { run } from './workflow/run.ts';

/** How long a scheduler child gets after SIGTERM before it is killed. */
export const SCHEDULER_CHILD_WAIT_MS = 10_000;

/** Discover PRs until interrupted, then stop the workers: they get SIGTERM and 10 s before SIGKILL. */
export async function runScheduler(settings: Settings, config: string, options: ServeOptions): Promise<number> {
  assertFileLockAvailable();
  const stop = new StopRequest();
  const runner = createProcessRunner(new ProcessSupervisor({ graceMs: SCHEDULER_CHILD_WAIT_MS }));
  try {
    return await serve(settings, config, options, {
      github: new Github(runner),
      spawn: (argv, spawnOptions) => runner.start(argv, spawnOptions),
      stop: () => stop.requested,
      wait: (seconds) => stop.sleep(seconds),
    });
  } finally {
    stop.dispose();
  }
}

/** Maintain one explicit PR; an interrupt records `interrupted` and surfaces as `Cancelled` (exit 130 or 143). */
export async function runWorker(settings: Settings, repo: string, number: number): Promise<number> {
  assertFileLockAvailable();
  const stop = new StopRequest();
  const runner = refusingAfter(stop, processRunner);
  try {
    return await run(settings, repo, number, {
      checkpoint: () => {
        stop.throwIfRequested();
      },
      git: new Git(runner),
      github: new Github(runner),
      verification: createVerification(runner),
      wait: async (seconds) => {
        await stop.sleep(seconds);
        stop.throwIfRequested();
      },
    });
  } finally {
    stop.dispose();
  }
}

/** A runner that starts nothing once the operator asked to stop, so a signal between children is never lost. */
function refusingAfter(stop: StopRequest, runner: ProcessRunner): ProcessRunner {
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
