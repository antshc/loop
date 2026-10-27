/** Blocking subprocess execution for dev-owned platform access. */

/** Runs one CLI call and resolves to its stdout. */
export type CliCall = (args: readonly string[]) => Promise<string>;

export interface CommandOptions {
  cwd?: string;
  timeoutS?: number;
}

export class CommandError extends Error {
  readonly command: string;
  readonly output: string;
  readonly returncode: null | number;

  constructor(command: string, returncode: null | number, output: string) {
    super(`command failed (${returncode === null ? 'None' : String(returncode)}): ${command}\n${output}`.trimEnd());
    this.name = 'CommandError';
    this.command = command;
    this.returncode = returncode;
    this.output = output;
  }
}

/** Binds an executable and working directory into a call taking only its arguments. */
export function cliRunner(executable: string, cwd?: string): CliCall {
  return (args) => runCommand([executable, ...args], { cwd });
}

/** Runs a process and resolves to stdout; rejects with CommandError on a non-zero exit, timeout, or missing executable. */
export async function runCommand(args: readonly string[], options: CommandOptions = {}): Promise<string> {
  const label = args.join(' ');
  let child: Bun.Subprocess<'ignore', 'pipe', 'pipe'>;
  try {
    child = Bun.spawn([...args], { cwd: options.cwd, stderr: 'pipe', stdin: 'ignore', stdout: 'pipe' });
  } catch (error) {
    throw new CommandError(label, null, error instanceof Error ? error.message : String(error));
  }
  const state = { timedOut: false };
  const timer =
    options.timeoutS === undefined
      ? null
      : setTimeout(() => {
          state.timedOut = true;
          child.kill();
        }, options.timeoutS * 1000);
  try {
    const [stdout, stderr, exitCode] = await Promise.all([child.stdout.text(), child.stderr.text(), child.exited]);
    if (state.timedOut) {
      throw new CommandError(label, null, `timed out after ${String(options.timeoutS)}s`);
    }
    if (exitCode !== 0) {
      throw new CommandError(label, exitCode, stdout + stderr);
    }
    return stdout;
  } finally {
    if (timer !== null) {
      clearTimeout(timer);
    }
  }
}
