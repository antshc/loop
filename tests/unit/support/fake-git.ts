import { ProcessFailedError, type ProcessOptions, type ProcessResult, type ProcessRunner, ProcessTimeoutError } from '../../../src/loop/run/process.ts';

export interface FakeGitOptions {
  /** Makes `git status` report uncommitted changes. */
  dirty?: boolean;
  /** Commands (as typed) whose shell hook exits non-zero. */
  failing?: readonly string[];
  /** Ref fragments `show-ref` reports as missing. */
  missing?: readonly string[];
  /** Makes `git worktree remove` fail. */
  removeFails?: boolean;
  /** Hook commands that time out. */
  timingOut?: readonly string[];
}

export interface HookCall {
  command: string;
  options: ProcessOptions | undefined;
}

/** Records git invocations and Loop hook shell calls; behaves like a clean repository unless told otherwise. */
export class FakeGitProcesses implements ProcessRunner {
  /** Every call as git arguments after `-C <repository>`, or `['hook', command]`. */
  readonly commands: string[][] = [];
  readonly hooks: HookCall[] = [];
  readonly repositories = new Set<string>();

  /** The first two words of each command, the way the Python tests compare them. */
  get names(): string[] {
    return this.commands.map((command) => command.slice(0, 2).join(' '));
  }

  constructor(private readonly behavior: FakeGitOptions = {}) {}

  run(argv: readonly string[], options?: ProcessOptions): Promise<ProcessResult> {
    const [program, flag, second, ...rest] = argv;
    if (program === '/bin/sh' && flag === '-c') {
      return this.#hook(second ?? '', argv, options);
    }
    this.repositories.add(second ?? '');
    this.commands.push(rest);
    if (this.behavior.removeFails === true && rest[0] === 'worktree' && rest[1] === 'remove') {
      return Promise.reject(new ProcessFailedError(result(argv, 1)));
    }
    if (rest[0] === 'status') {
      return Promise.resolve(result(argv, 0, this.behavior.dirty === true ? ' M cart.txt\n' : ''));
    }
    const missing = rest[0] === 'show-ref' && (this.behavior.missing ?? []).some((ref) => (rest.at(-1) ?? '').includes(ref));
    return Promise.resolve(result(argv, missing ? 1 : 0));
  }

  start(): never {
    throw new Error('not used');
  }

  #hook(command: string, argv: readonly string[], options: ProcessOptions | undefined): Promise<ProcessResult> {
    this.hooks.push({ command, options });
    this.commands.push(['hook', command]);
    if (this.behavior.timingOut?.includes(command) === true) {
      return Promise.reject(new ProcessTimeoutError(argv, options?.timeoutSec ?? 0, '', ''));
    }
    if (this.behavior.failing?.includes(command) === true) {
      return Promise.resolve({ ...result(argv, 1, 'out-'), stderr: 'err' });
    }
    return Promise.resolve(result(argv, 0));
  }
}

function result(argv: readonly string[], exitCode: number, stdout = ''): ProcessResult {
  return { argv, exitCode, signal: null, stderr: '', stdout };
}
