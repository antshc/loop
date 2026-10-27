import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const harness = join(import.meta.dir, 'support', 'cancel-harness.ts');

let directory = '';

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-cancel-'));
});

afterEach(() => {
  for (const pid of pidsOf()) {
    try {
      process.kill(pid, 'SIGKILL');
    } catch {
      // already gone
    }
  }
  rmSync(directory, { force: true, recursive: true });
});

describe('cancellation of a running process tree', () => {
  test('SIGINT while blocked on a child kills it, its shell grandchildren and SIGTERM-ignoring children, then exits 130', async () => {
    const run = await start('stubborn');

    run.child.kill('SIGINT');

    expect(await run.child.exited).toBe(130);
    expect(pidsOf().length).toBeGreaterThanOrEqual(2);
    expectAllDead();
    expect(markers()).toEqual(['START', 'READY', 'CLEANUP_START', 'CLEANUP_DONE']);
  });

  test('SIGTERM runs the same cleanup and exits 143', async () => {
    const run = await start('grandchild');

    run.child.kill('SIGTERM');

    expect(await run.child.exited).toBe(143);
    expectAllDead();
    expect(markers()).toEqual(['START', 'READY', 'CLEANUP_START', 'CLEANUP_DONE']);
    expect(existsSync(join(directory, 'worktree'))).toBe(false);
  });

  test('a direct child is killed with exit 130', async () => {
    const run = await start('direct');

    run.child.kill('SIGINT');

    expect(await run.child.exited).toBe(130);
    expectAllDead();
  });

  test('Ctrl-C reaching the child group as well as Loop cleans up once with the same end state', async () => {
    const run = await start('grandchild');
    const [leader] = pidsOf();

    process.kill(-(leader ?? 0), 'SIGINT');
    run.child.kill('SIGINT');

    expect(await run.child.exited).toBe(130);
    expectAllDead();
    expect(markers()).toEqual(['START', 'READY', 'CLEANUP_START', 'CLEANUP_DONE']);
  });

  test('a second signal during cleanup force-kills remaining groups and exits at once', async () => {
    const run = await start('stubborn', 'slow');

    run.child.kill('SIGINT');
    await waitFor(() => markers().includes('CLEANUP_CHILD'));
    const started = Date.now();
    run.child.kill('SIGTERM');

    expect(await run.child.exited).toBe(143);
    expect(Date.now() - started).toBeLessThan(2000);
    expectAllDead();
    expect(markers()).not.toContain('CLEANUP_DONE');
  });

  test('re-running cleanup after an interrupted cleanup ends in the same state', async () => {
    const interrupted = await start('grandchild', 'slow');
    interrupted.child.kill('SIGINT');
    await waitFor(() => markers().includes('CLEANUP_CHILD'));
    interrupted.child.kill('SIGINT');
    await interrupted.child.exited;
    expect(existsSync(join(directory, 'worktree'))).toBe(true);

    rmSync(join(directory, 'log'));
    const rerun = await start('grandchild');
    rerun.child.kill('SIGINT');

    expect(await rerun.child.exited).toBe(130);
    expect(existsSync(join(directory, 'worktree'))).toBe(false);
    expectAllDead();
  });
});

interface Run {
  child: Bun.Subprocess;
}

function expectAllDead(): void {
  for (const pid of pidsOf()) {
    expect([pid, isAlive(pid)]).toEqual([pid, false]);
  }
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function markers(): string[] {
  return read('log').split('\n').filter(Boolean);
}

function pidsOf(): number[] {
  return read('pids').split('\n').filter(Boolean).map(Number);
}

function read(name: string): string {
  const path = join(directory, name);
  return existsSync(path) ? readFileSync(path, 'utf8') : '';
}

async function start(kind: string, cleanup = 'quick'): Promise<Run> {
  const child = Bun.spawn(['bun', harness, kind, directory, cleanup], { stderr: 'inherit', stdout: 'inherit' });
  await waitFor(() => markers().includes('READY'));
  return { child };
}

async function waitFor(condition: () => boolean, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) {
      throw new Error('timed out waiting for the condition');
    }
    await Bun.sleep(20);
  }
}
