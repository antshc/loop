import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const harness = join(import.meta.dir, 'support', 'review-cancel-harness.ts');

let directory = '';
let repository = '';

const git = (...args: string[]): string => {
  const result = Bun.spawnSync(['git', '-C', repository, ...args], { stderr: 'pipe', stdout: 'pipe' });
  if (result.exitCode !== 0) {
    throw new Error(`git ${args.join(' ')} failed: ${result.stderr.toString()}`);
  }
  return result.stdout.toString().trim();
};

const read = (name: string): string => (existsSync(join(directory, name)) ? readFileSync(join(directory, name), 'utf8') : '');

async function waitFor(condition: () => boolean, timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) {
      throw new Error('timed out waiting for the condition');
    }
    await Bun.sleep(20);
  }
}

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'dev-review-cancel-'));
  repository = join(directory, 'repo');
  mkdirSync(repository);
  git('init');
  git('config', 'user.name', 'Test');
  git('config', 'user.email', 'test@example.com');
  git('config', 'commit.gpgsign', 'false');
  writeFileSync(join(repository, 'basket.txt'), 'initial');
  git('add', '-A');
  git('commit', '-m', 'initial');
  mkdirSync(join(directory, 'bin'));
  // The reviewer CLI: records where it runs, then blocks like a long review.
  writeFileSync(join(directory, 'bin', 'copilot'), `#!/bin/sh\npwd > '${join(directory, 'ready')}'\nexec sleep 300\n`);
  chmodSync(join(directory, 'bin', 'copilot'), 0o755);
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

describe('a review cancelled by a signal', () => {
  for (const [signal, code] of [
    ['SIGINT', 130],
    ['SIGTERM', 143],
  ] as const) {
    test(`${signal} ends the review phase cancelled and removes the clean worktree`, async () => {
      const child = Bun.spawn([process.execPath, harness, directory, repository], {
        env: { ...process.env, PATH: `${join(directory, 'bin')}:${process.env.PATH ?? ''}` },
        stderr: 'inherit',
        stdout: 'inherit',
      });
      await waitFor(() => read('ready') !== '');
      const worktree = read('ready').trim();
      expect(existsSync(worktree)).toBe(true);

      child.kill(signal);

      expect(await child.exited).toBe(code);
      expect(read('log')).toContain('STATUS cancelled');
      expect(read('log')).toContain('LOG Review pull request · spec #1 cancelled');
      expect(read('log')).not.toContain('COMPLETED');
      expect(existsSync(worktree)).toBe(false);
      expect(git('worktree', 'list').split('\n')).toHaveLength(1);
    });
  }
});
