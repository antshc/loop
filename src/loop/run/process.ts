import { constants } from 'node:os';

import { Cancelled } from './cancelled.ts';
import { ProcessSupervisor } from './supervisor.ts';

export interface ProcessHandle {
  readonly pid: number;
  /** Exit code once the process has ended, otherwise null. */
  poll: () => null | number;
  /** SIGTERM to the process group, SIGKILL after the grace period. */
  terminate: () => void;
  wait: () => Promise<ProcessResult>;
}

export interface ProcessOptions {
  /** Raise ProcessFailedError on a non-zero exit. */
  check?: boolean;
  cwd?: string;
  /** Merged over the parent environment; an `undefined` value removes the variable. */
  env?: Readonly<Record<string, string | undefined>>;
  timeoutSec?: number;
}

export interface ProcessResult {
  readonly argv: readonly string[];
  readonly exitCode: number;
  /** Name of the signal that ended the process, if any. */
  readonly signal: null | string;
  readonly stderr: string;
  readonly stdout: string;
}

/** The injectable seam the CLI runner, git service and docker service run processes through. */
export interface ProcessRunner {
  run: (argv: readonly string[], options?: ProcessOptions) => Promise<ProcessResult>;
  start: (argv: readonly string[], options?: ProcessOptions) => ProcessHandle;
}

export class ProcessFailedError extends Error {
  readonly argv: readonly string[];
  readonly exitCode: number;
  readonly stderr: string;
  readonly stdout: string;

  constructor(result: ProcessResult) {
    super(`Command '${result.argv.join(' ')}' returned non-zero exit status ${String(result.exitCode)}.`);
    this.name = 'ProcessFailedError';
    this.argv = result.argv;
    this.exitCode = result.exitCode;
    this.stdout = result.stdout;
    this.stderr = result.stderr;
  }
}

/** Library-internal: callers translate it into their own error. */
export class ProcessTimeoutError extends Error {
  readonly argv: readonly string[];
  readonly stderr: string;
  readonly stdout: string;
  readonly timeoutSec: number;

  constructor(argv: readonly string[], timeoutSec: number, stdout: string, stderr: string) {
    super(`Command '${argv.join(' ')}' timed out after ${String(timeoutSec)} seconds.`);
    this.name = 'ProcessTimeoutError';
    this.argv = argv;
    this.timeoutSec = timeoutSec;
    this.stdout = stdout;
    this.stderr = stderr;
  }
}

// A child that dies from SIGINT/SIGTERM and Loop itself receive the signal almost together.
const SIGNAL_RACE_WINDOW_MS = 50;
const INTERRUPT_EXIT_CODES: readonly number[] = [130, 143];

const defaultSupervisor = new ProcessSupervisor();

export function createProcessRunner(supervisor: ProcessSupervisor = defaultSupervisor): ProcessRunner {
  return {
    run: (argv, options) => Promise.resolve().then(() => start(supervisor, argv, options ?? {}).wait()),
    start: (argv, options) => start(supervisor, argv, options ?? {}),
  };
}

/** The process-wide runner, backed by the shared supervisor. */
export const processRunner: ProcessRunner = createProcessRunner();

function mergeEnvironment(overrides: Readonly<Record<string, string | undefined>> = {}): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const [name, value] of Object.entries({ ...process.env, ...overrides })) {
    if (value !== undefined) {
      merged[name] = value;
    }
  }
  return merged;
}

function pollExitCode(child: { exitCode: null | number; signalCode: null | number | string }): null | number {
  if (child.exitCode !== null) {
    return child.exitCode;
  }
  const name = signalName(child.signalCode);
  if (name === null) {
    return null;
  }
  const signals: Readonly<Record<string, number | undefined>> = constants.signals;
  return 128 + (signals[name] ?? 0);
}

async function readText(stream: ReadableStream<Uint8Array>): Promise<string> {
  const bytes = await new Response(stream).arrayBuffer();
  return new TextDecoder('utf-8', { fatal: false, ignoreBOM: true }).decode(bytes);
}

function signalName(code: null | number | string): null | string {
  if (typeof code !== 'number') {
    return code;
  }
  const signals: Readonly<Record<string, number | undefined>> = constants.signals;
  return Object.keys(signals).find((name) => signals[name] === code) ?? null;
}

function start(supervisor: ProcessSupervisor, argv: readonly string[], options: ProcessOptions): ProcessHandle {
  const child = Bun.spawn([...argv], {
    cwd: options.cwd,
    detached: true,
    env: mergeEnvironment(options.env),
    stderr: 'pipe',
    stdin: 'ignore',
    stdout: 'pipe',
  });
  const pgid = child.pid;
  const lease = supervisor.track(pgid);
  let timedOut = false;
  const timer =
    options.timeoutSec === undefined
      ? undefined
      : setTimeout(() => {
          timedOut = true;
          void supervisor.terminate(pgid);
        }, options.timeoutSec * 1000);

  const settle = async (): Promise<ProcessResult> => {
    try {
      const [exitCode, stdout, stderr] = await Promise.all([child.exited, readText(child.stdout), readText(child.stderr)]);
      await supervisor.terminating(pgid);
      if (INTERRUPT_EXIT_CODES.includes(exitCode) && lease.cancelledBy === undefined) {
        await Bun.sleep(SIGNAL_RACE_WINDOW_MS);
      }
      if (lease.cancelledBy !== undefined) {
        await supervisor.terminate(pgid);
        throw new Cancelled(lease.cancelledBy);
      }
      if (timedOut && options.timeoutSec !== undefined) {
        await supervisor.terminate(pgid);
        throw new ProcessTimeoutError(argv, options.timeoutSec, stdout, stderr);
      }
      const result: ProcessResult = { argv, exitCode, signal: signalName(child.signalCode), stderr, stdout };
      if (options.check === true && exitCode !== 0) {
        throw new ProcessFailedError(result);
      }
      return result;
    } finally {
      clearTimeout(timer);
      lease.release();
    }
  };

  const result = settle();
  // A fire-and-forget caller may never wait; the rejection must not become unhandled.
  result.catch(() => undefined);
  return {
    pid: pgid,
    poll: () => pollExitCode(child),
    terminate: () => {
      void supervisor.terminate(pgid);
    },
    wait: () => result,
  };
}
