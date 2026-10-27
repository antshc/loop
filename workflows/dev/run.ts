/** The dev run: composes the container, configures logging, and delivers. */
import { join } from 'node:path';

import type { DevOptions } from './args.ts';

import { configureLogging, type OutputStream } from '../../src/loop/index.ts';
import { composeDev } from './composition.ts';
import { type DevDependencies } from './dependencies.ts';
import { DevRunner, failureCode, highLevelOnly } from './runner.ts';
import { LOG_DIRECTORY } from './tokens.ts';

export type { DevDependencies } from './dependencies.ts';

/** Delivers harness Specs through configured repositories; resolves to the exit code. */
export async function runDev(options: DevOptions, dependencies: DevDependencies, stderr: OutputStream): Promise<number> {
  const container = composeDev(options, dependencies, stderr);
  // The workflow is the application: it owns every logger so its own records land in the same file.
  configureLogging({
    level: options.logLevel,
    logFile: join(container.get<string>(LOG_DIRECTORY), 'dev.log'),
    logger: '',
    stream: { write: (text) => { highLevelOnly(stderr, text); } },
  });
  let runner: DevRunner;
  try {
    runner = container.get(DevRunner);
  } catch (error) {
    return failureCode(error);
  }
  return runner.run();
}
