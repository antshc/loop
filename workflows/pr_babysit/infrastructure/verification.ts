import type { Repository } from '../settings.ts';

import { type ProcessRunner, processRunner } from '../../../src/loop/index.ts';

/** Gate publication on the configured build and test commands; a failing command rejects with `ProcessFailedError`. */
export type Verify = (path: string, repository: Repository, timeoutSec: number) => Promise<void>;

/** Run workflow-configured build and test commands without shell interpolation. */
export function createVerification(runner: ProcessRunner = processRunner): Verify {
  return async (path, repository, timeoutSec) => {
    for (const command of [repository.build, repository.test]) {
      await runner.run(command, { check: true, cwd: path, timeoutSec });
    }
  };
}
