import { Command, CommanderError } from 'commander';

import { enforceRuntime, ValueError } from '../../src/loop/index.ts';
import { Blocked } from './infrastructure/state.ts';
import { resolvePath, Settings } from './settings.ts';

const PR_URL = /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/([0-9]+)\/?$/;

/** Everything `main` touches outside its own parsing; tests and the entry point supply the doubles or real parts. */
export interface MainDeps {
  /** Operator-facing diagnostics, one message per call. */
  error: (message: string) => void;
  /** Maintain one explicit PR; resolves to the process exit code. */
  run: (settings: Settings, repo: string, number: number) => Promise<number>;
  /** Discover PRs continuously; resolves to the process exit code. */
  serve: (settings: Settings, config: string, options: ServeOptions) => Promise<number>;
  stderr: (text: string) => void;
  stdout: (text: string) => void;
}

export interface ServeOptions {
  readonly includeExisting: boolean;
  readonly retry: string | undefined;
}

const notWired = (): Promise<number> => Promise.reject(new Error('pr_babysit execution is not wired to this entry point'));

const DEFAULT_DEPS: MainDeps = {
  error: (message) => {
    process.stderr.write(`PR maintenance stopped: ${message}\n`);
  },
  run: notWired,
  serve: notWired,
  stderr: (text) => process.stderr.write(text),
  stdout: (text) => process.stdout.write(text),
};

/**
 * Start discovery or maintain one explicit PR with the same configuration.
 * Resolves to the exit code: 0 on success or `--help`, 1 for a blocked or unreadable configuration, 2 for bad input.
 */
export async function main(argv: readonly string[], deps: Partial<MainDeps> = {}): Promise<number> {
  const { error, run, serve, stderr, stdout } = { ...DEFAULT_DEPS, ...deps };
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
    if (repo !== undefined && number !== undefined) return await run(settings, repo, Number(number));
    return await serve(settings, resolvePath(options.config), {
      includeExisting: options.includeExisting === true,
      retry: options.retry,
    });
  } catch (failure) {
    if (failure instanceof Blocked || failure instanceof ValueError || isOsError(failure)) {
      error(failure.message);
      return 1;
    }
    throw failure;
  }
}

/** A filesystem or process failure carrying an errno code, the TypeScript counterpart of Python's `OSError`. */
function isOsError(value: unknown): value is Error {
  return value instanceof Error && 'code' in value && typeof value.code === 'string' && /^E[A-Z0-9]+$/.test(value.code);
}

if (import.meta.main) {
  enforceRuntime();
  process.exitCode = await main(Bun.argv.slice(2));
}
