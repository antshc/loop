import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { main, normalise, type ShimEnvironment } from '../../src/loop/hooks/shim.ts';

interface GoldenCase {
  cli: string;
  env: string;
  name: string;
  point: string;
  received: string;
  session: string;
  stdin: string;
}

const SHIM = join(import.meta.dir, '..', '..', 'src', 'loop', 'hooks', 'shim.ts');
const GOLDEN = (await Bun.file(join(import.meta.dir, '..', 'fixtures', 'hook-payload.json')).json()) as GoldenCase[];

let directory = '';

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-shim-'));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

const run = async (args: readonly string[], stdin: string): Promise<{ exitCode: number; stderr: string; stdout: string }> => {
  const child = Bun.spawn([process.execPath, SHIM, ...args], { stderr: 'pipe', stdin: new TextEncoder().encode(stdin), stdout: 'pipe' });
  const [exitCode, stdout, stderr] = await Promise.all([child.exited, new Response(child.stdout).text(), new Response(child.stderr).text()]);
  return { exitCode, stderr, stdout };
};

describe('the hook payload normaliser', () => {
  test('maps Copilot camelCase and Codex snake_case payloads to one shape', () => {
    const copilot = normalise('copilot', 'session-start', { cwd: '/w', sessionId: 's', source: 'resume' });
    const codex = normalise('codex', 'session-start', { cwd: '/w', session_id: 's', source: 'resume' });

    expect(copilot).toEqual({ ...codex, cli: 'copilot' });
    expect(copilot.resumed).toBe(true);
    expect(copilot.handle).toBe('s');
    expect(normalise('codex', 'pre-tool', { tool_name: 'bash' }).tool).toBe('bash');
  });

  test('lists the fields in the documented order', () => {
    expect(Object.keys(normalise('copilot', 'session-end', {}))).toEqual([
      'point',
      'cli',
      'handle',
      'cwd',
      'resumed',
      'reason',
      'tool',
    ]);
  });
});

describe('the hook payload golden fixture captured from the Python shim', () => {
  for (const golden of GOLDEN) {
    test(`a real shim process delivers the same payload and variables: ${golden.name}`, async () => {
      const received = join(directory, 'stdin');
      const env = join(directory, 'env');
      const command = `cat > "${received}"; printf "%s|%s|%s" "$LOOP_SESSION" "$LOOP_CLI" "$LOOP_HOOK_POINT" > "${env}"`;

      const result = await run(['--cli', golden.cli, '--point', golden.point, '--session', golden.session, '--', command], golden.stdin);

      expect(result.exitCode).toBe(0);
      expect(result.stdout).toBe('');
      expect(readFileSync(received, 'utf8')).toBe(golden.received);
      expect(readFileSync(env, 'utf8')).toBe(golden.env);
    });
  }
});

describe('the hook shim fails open', () => {
  test('a failing command exits 0 and reports only on stderr', async () => {
    const result = await run(['--cli', 'copilot', '--point', 'session-end', '--session', 's', '--', 'echo noise; exit 3'], '{}');

    expect(result).toEqual({ exitCode: 0, stderr: 'agent-cli-hook: session-end command exited 3\n', stdout: '' });
  });

  test('a command that never reads its input does not break the shim', async () => {
    const result = await run(['--cli', 'codex', '--point', 'pre-tool', '--session', 's', '--', 'true'], 'x'.repeat(200_000));

    expect(result.exitCode).toBe(0);
  });

  test('missing arguments exit 0 with a diagnostic', async () => {
    const result = await run(['--cli', 'copilot'], '{}');

    expect(result.exitCode).toBe(0);
    expect(result.stderr).toContain('agent-cli-hook:');
    expect(result.stdout).toBe('');
  });

  test('a command that cannot be found exits 0', async () => {
    const result = await run(['--cli', 'copilot', '--point', 'session-end', '--session', 's', '--', 'nonexistent-command-xyz'], '{}');

    expect(result.exitCode).toBe(0);
  });

  test('an internal failure resolves to 0 and is reported, never thrown', async () => {
    const reported: string[] = [];
    const environment: ShimEnvironment = {
      readStdin: () => Promise.reject(new Error('stdin broke')),
      report: (line) => reported.push(line),
      runShell: () => Promise.resolve(0),
    };

    const code = await main(['--cli', 'copilot', '--point', 'session-end', '--session', 's', '--', 'true'], environment);

    expect(code).toBe(0);
    expect(reported).toEqual(['agent-cli-hook: Error("stdin broke")']);
  });

  test('a shell that cannot start resolves to 0', async () => {
    const environment: ShimEnvironment = {
      readStdin: () => Promise.resolve('{}'),
      report: () => undefined,
      runShell: () => Promise.reject(new Error('spawn failed')),
    };

    expect(await main(['--cli', 'c', '--point', 'p', '--session', 's', '--', 'true'], environment)).toBe(0);
  });
});

describe('the hook command line', () => {
  test('joins the command words with spaces and omits the separator', async () => {
    const commands: string[] = [];
    const environment: ShimEnvironment = {
      readStdin: () => Promise.resolve('{}'),
      report: () => undefined,
      runShell: (command) => {
        commands.push(command);
        return Promise.resolve(0);
      },
    };

    await main(['--cli', 'copilot', '--point', 'session-end', '--session', 's', '--', 'my', 'cmd'], environment);

    expect(commands).toEqual(['my cmd']);
  });
});
