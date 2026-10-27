import { Command, CommanderError } from 'commander';

import { configureLogging, enforceRuntime, runEntrypoint, ValueError } from '../../src/loop/index.ts';
import { createContainer, type MainDeps } from './composition.ts';
import { type DiscoveryService } from './discovery.ts';
import { FileLockUnavailableError } from './file-lock.ts';
import { isOsError } from './infrastructure/os-error.ts';
import { Blocked } from './infrastructure/state.ts';
import { resolvePath, Settings } from './settings.ts';
import { TOKENS } from './tokens.ts';
import { type WorkerService } from './worker.ts';

export type { MainDeps } from './composition.ts';
export type { ServeOptions } from './discovery.ts';

const PR_URL = /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/([0-9]+)\/?$/;

/**
 * Start discovery or maintain one explicit PR with the same configuration.
 * Resolves to the exit code: 0 on success or `--help`, 1 for a blocked or unreadable configuration, 2 for bad input.
 */
export async function main(argv: readonly string[], deps: MainDeps = {}): Promise<number> {
  const container = createContainer(deps);
  const error = container.get<(message: string) => void>(TOKENS.ErrorReporter);
  const stderr = container.get<(text: string) => void>(TOKENS.StderrWriter);
  const stdout = container.get<(text: string) => void>(TOKENS.StdoutWriter);
  const program = new Command('pr_babysit')
    .description('Discover PRs continuously or babysit one PR')
    .requiredOption('--config <path>')
    .option('--pr <url>', 'https://github.com/owner/repo/pull/123')
    .option('--include-existing')
    .option('--retry <url>', 'Retry an enrolled blocked PR during discovery')
    .exitOverride()
    .configureOutput({
      outputError: (text) => {
        stderr(`Usage: ${program.name()} ${program.usage()}\n${text}`);
      },
      writeErr: stderr,
      writeOut: stdout,
    });
  try {
    program.parse([...argv], { from: 'user' });
  } catch (failure) {
    if (failure instanceof CommanderError) return failure.exitCode === 0 ? 0 : 2;
    throw failure;
  }
  const options = program.opts<{ config: string; includeExisting?: true; pr?: string; retry?: string }>();
  const match = options.pr ? PR_URL.exec(options.pr) : null;
  const usageError = (message: string): number => {
    stderr(`Usage: ${program.name()} ${program.usage()}\nerror: ${message}\n`);
    return 2;
  };
  if (options.pr && !match) return usageError('--pr must be a GitHub PR URL');
  if (options.pr && (options.includeExisting === true || options.retry)) {
    return usageError('--include-existing and --retry apply to discovery only');
  }
  try {
    const settings = Settings.load(options.config);
    const [, repo, number] = match ?? [];
    if (repo !== undefined && number !== undefined) return await container.get<WorkerService>(TOKENS.WorkerService).run(settings, repo, Number(number));
    return await container.get<DiscoveryService>(TOKENS.DiscoveryService).serve(settings, resolvePath(options.config), {
      includeExisting: options.includeExisting === true,
      retry: options.retry,
    });
  } catch (failure) {
    if (
      failure instanceof Blocked ||
      failure instanceof FileLockUnavailableError ||
      failure instanceof ValueError ||
      isOsError(failure)
    ) {
      error(failure.message);
      return 1;
    }
    throw failure;
  }
}

if (import.meta.main) {
  enforceRuntime();
  configureLogging({ level: 'info', logger: 'pr_babysit' });
  await runEntrypoint(async () => {
    process.exitCode = await main(Bun.argv.slice(2));
  });
}
