/**
 * Agent CLI hook shim: normalises a CLI's native hook payload for a user command.
 *
 * Usage: bun shim.ts --cli <name> --point <point> --session <SessionName> -- <command>
 * Observe-only: it discards the command's stdout and always exits 0.
 */
import { dumps, type Json } from '../json.ts';
import { describeError } from '../text.ts';

export interface ShimArguments {
  readonly cli: string;
  readonly command: readonly string[];
  readonly point: string;
  readonly session: string;
}

/** Where the shim reads its payload and reports; injectable for testing. */
export interface ShimEnvironment {
  readStdin: () => Promise<string>;
  report: (line: string) => void;
  /** Runs the shell command with `input` on stdin, its stdout discarded; resolves to its exit code. */
  runShell: (command: string, input: string, env: Readonly<Record<string, string>>) => Promise<number>;
}

/** Always resolves to 0: a hook must never break the CLI run. */
export async function main(argv: readonly string[], environment: ShimEnvironment = defaultEnvironment()): Promise<number> {
  try {
    const args = parseArguments(argv);
    const raw = parsePayload(await environment.readStdin());
    const payload = { ...normalise(args.cli, args.point, raw), session: args.session };
    const env = { LOOP_CLI: args.cli, LOOP_HOOK_POINT: args.point, LOOP_SESSION: args.session };
    const exitCode = await environment.runShell(args.command.join(' '), `${dumps(payload)}\n`, env);
    if (exitCode !== 0) {
      environment.report(`agent-cli-hook: ${args.point} command exited ${String(exitCode)}`);
    }
  } catch (error) {
    environment.report(`agent-cli-hook: ${describeError(error)}`);
  }
  return 0;
}

/** Maps Copilot (camelCase) and Codex (snake_case) payloads to one shape; `session` is added by `main`. */
export function normalise(cli: string, point: string, raw: Readonly<Record<string, unknown>>): Record<string, Json> {
  // Entries, not an object literal: the payload's field order is part of the contract.
  return Object.fromEntries([
    ['point', point],
    ['cli', cli],
    ['handle', first(raw, 'sessionId', 'session_id')],
    ['cwd', first(raw, 'cwd')],
    ['resumed', raw.source === 'resume'],
    ['reason', first(raw, 'reason')],
    ['tool', first(raw, 'toolName', 'tool_name')],
  ]);
}

function defaultEnvironment(): ShimEnvironment {
  return {
    readStdin: () => Bun.stdin.text(),
    report: (line) => {
      process.stderr.write(`${line}\n`);
    },
    runShell: async (command, input, env) => {
      const child = Bun.spawn(['/bin/sh', '-c', command], {
        env: { ...process.env, ...env },
        stderr: 'ignore',
        stdin: new TextEncoder().encode(input),
        stdout: 'ignore',
      });
      return child.exited;
    },
  };
}

function first(raw: Readonly<Record<string, unknown>>, ...keys: string[]): Json {
  const key = keys.find((candidate) => Object.hasOwn(raw, candidate));
  return key === undefined ? null : (raw[key] as Json);
}

function parseArguments(argv: readonly string[]): ShimArguments {
  const values = new Map<string, string>();
  let index = 0;
  while (index < argv.length) {
    const flag = argv[index] ?? '';
    if (flag !== '--cli' && flag !== '--point' && flag !== '--session') {
      break;
    }
    const value = argv[index + 1];
    if (value === undefined) {
      throw new Error(`argument ${flag}: expected one argument`);
    }
    values.set(flag, value);
    index += 2;
  }
  const rest = argv.slice(index);
  const command = rest[0] === '--' ? rest.slice(1) : rest;
  const cli = values.get('--cli');
  const point = values.get('--point');
  const session = values.get('--session');
  if (cli === undefined || point === undefined || session === undefined) {
    throw new Error('the following arguments are required: --cli, --point, --session');
  }
  return { cli, command, point, session };
}

function parsePayload(text: string): Record<string, unknown> {
  let raw: unknown;
  try {
    raw = JSON.parse(text === '' ? '{}' : text);
  } catch {
    return {};
  }
  return typeof raw === 'object' && raw !== null && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
}

if (import.meta.main) {
  process.exitCode = await main(process.argv.slice(2));
}
