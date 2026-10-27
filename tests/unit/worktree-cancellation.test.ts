import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const harness = join(import.meta.dir, 'support', 'worktree-cancel-harness.ts');

let directory = '';
let repository = '';

const git = (...args: string[]): string => {
  const result = Bun.spawnSync(['git', '-C', repository, ...args], { stderr: 'pipe', stdout: 'pipe' });
  if (result.exitCode !== 0) {
    throw new Error(`git ${args.join(' ')} failed: ${result.stderr.toString()}`);
  }
  return result.stdout.toString().trim();
};

const marks = (): string[] =>
  existsSync(join(directory, 'log'))
    ? readFileSync(join(directory, 'log'), 'utf8')
        .split('\n')
        .filter((line) => line !== '')
    : [];

const start = async (mode: 'clean' | 'dirty'): Promise<{ child: Bun.Subprocess; path: string }> => {
  const child = Bun.spawn([process.execPath, harness, directory, repository, mode], { stderr: 'inherit', stdout: 'inherit' });
  const deadline = Date.now() + 10_000;
  while (!marks().some((line) => line.startsWith('READY '))) {
    if (Date.now() > deadline) {
      child.kill('SIGKILL');
      throw new Error('harness never became ready');
    }
    await Bun.sleep(20);
  }
  return { child, path: (marks().find((line) => line.startsWith('READY ')) ?? '').slice('READY '.length) };
};

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-worktree-cancel-'));
  repository = join(directory, 'repo');
  mkdirSync(repository);
  git('init');
  git('config', 'user.name', 'Test');
  git('config', 'user.email', 'test@example.com');
  git('config', 'commit.gpgsign', 'false');
  writeFileSync(join(repository, 'cart.txt'), 'initial');
  git('add', '-A');
  git('commit', '-m', 'initial');
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

describe('worktree cleanup on SIGINT and SIGTERM', () => {
  const signals: ['SIGINT' | 'SIGTERM', number][] = [
    ['SIGINT', 130],
    ['SIGTERM', 143],
  ];

  for (const [signal, code] of signals) {
    test(`${signal} while the body blocks on a child removes a clean worktree and fires neither removal hooks nor run-finished`, async () => {
      const run = await start('clean');
      expect(existsSync(run.path)).toBe(true);

      run.child.kill(signal);

      expect(await run.child.exited).toBe(code);
      expect(existsSync(run.path)).toBe(false);
      expect(git('worktree', 'list').split('\n')).toHaveLength(1);
      expect(marks().filter((line) => !line.startsWith('READY '))).toEqual([]);
    });

    test(`${signal} keeps a dirty worktree and its changes`, async () => {
      const run = await start('dirty');

      run.child.kill(signal);

      expect(await run.child.exited).toBe(code);
      expect(readFileSync(join(run.path, 'dirty.txt'), 'utf8')).toBe('unfinished');
      expect(marks().filter((line) => !line.startsWith('READY '))).toEqual([]);
    });
  }

  test('a cancelled run can be resumed and cleaned up again with the same end state', async () => {
    const run = await start('dirty');
    run.child.kill('SIGINT');
    await run.child.exited;
    rmSync(join(run.path, 'dirty.txt'));

    const again = Bun.spawn([process.execPath, harness, directory, repository, 'clean'], { stderr: 'inherit', stdout: 'inherit' });
    const deadline = Date.now() + 10_000;
    while (marks().filter((line) => line.startsWith('READY ')).length < 2 && Date.now() < deadline) {
      await Bun.sleep(20);
    }
    again.kill('SIGTERM');

    expect(await again.exited).toBe(143);
    expect(existsSync(run.path)).toBe(false);
    expect(git('worktree', 'list').split('\n')).toHaveLength(1);
  });
});
