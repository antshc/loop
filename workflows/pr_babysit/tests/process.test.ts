import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';

import { processLock } from '../infrastructure/state.ts';
import { PrState } from '../infrastructure/state.ts';

const MAIN = join(import.meta.dir, '..', 'main.ts');
const FAKE_GH = join(import.meta.dir, 'support', 'fake-gh.ts');
const LOCK_HOLDER = join(import.meta.dir, 'support', 'lock-process.ts');
const TIMEOUT_MS = 60_000;

interface Cli {
  readonly process: Bun.Subprocess<'ignore', 'pipe', 'pipe'>;
  readonly stderr: Promise<string>;
}

let root: string;
let stateDir: string;
let config: string;
const started: { exited: Promise<number>; kill: (signal?: NodeJS.Signals | number) => void }[] = [];
const strayPids: number[] = [];

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-process-')));
  stateDir = join(root, 'state');
  const repo = join(root, 'repo');
  const bin = join(root, 'bin');
  mkdirSync(repo);
  mkdirSync(bin);
  for (const args of [['init'], ['remote', 'add', 'origin', 'https://github.com/a/b.git']]) {
    expect(Bun.spawnSync(['git', '-C', repo, ...args]).exitCode).toBe(0);
  }
  writeFileSync(join(bin, 'gh'), `#!/bin/sh\nexec "${process.execPath}" "${FAKE_GH}" "$@"\n`);
  chmodSync(join(bin, 'gh'), 0o755);
  config = join(root, 'config.json');
  writeFileSync(
    config,
    JSON.stringify({
      author: 'me',
      concurrency: 1,
      interval: 0.2,
      repositories: [{ build: ['true'], name: 'a/b', path: 'repo', test: ['true'] }],
      state_dir: 'state',
    }),
  );
});

afterEach(async () => {
  for (const child of started.splice(0)) {
    child.kill('SIGKILL');
    await child.exited;
  }
  for (const pid of strayPids.splice(0)) {
    try {
      process.kill(pid, 'SIGKILL');
    } catch {
      // Already gone.
    }
  }
  rmSync(root, { force: true, recursive: true });
});

function launch(args: readonly string[], environment: Record<string, string> = {}): Cli {
  const child = Bun.spawn([process.execPath, MAIN, '--config', config, ...args], {
    env: { ...process.env, ...environment, PATH: `${join(root, 'bin')}${delimiter}${process.env.PATH ?? ''}` },
    stderr: 'pipe',
    stdin: 'ignore',
    stdout: 'pipe',
  });
  started.push(child);
  void new Response(child.stdout).text();
  return { process: child, stderr: new Response(child.stderr).text() };
}

async function waitFor(condition: () => boolean, what: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}`);
    await Bun.sleep(25);
  }
}

const prStatus = (): string | undefined => {
  const path = new PrState(stateDir, 'a/b', 1).path;
  return existsSync(path) ? (JSON.parse(readFileSync(path, 'utf8')) as { status: string }).status : undefined;
};

const isAlive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

/** A process that takes `lockPath` and holds it until killed. */
async function holdLock(lockPath: string): Promise<Bun.Subprocess<'ignore', 'pipe', 'inherit'>> {
  const holder = Bun.spawn([process.execPath, LOCK_HOLDER, 'hold', lockPath], { stderr: 'inherit', stdin: 'ignore', stdout: 'pipe' });
  started.push(holder);
  const reader = holder.stdout.getReader();
  const first = await reader.read();
  reader.releaseLock();
  expect(new TextDecoder().decode(first.value)).toContain('"acquired"');
  return holder;
}

const workerLock = (): string => new PrState(stateDir, 'a/b', 1).lockPath;
const schedulerLock = (): string => join(stateDir, 'scheduler.lock');

describe('a lock held by another process', () => {
  test('stops the scheduler as Blocked with exit code 1', async () => {
    await holdLock(schedulerLock());

    const cli = launch([]);

    expect(await cli.process.exited).toBe(1);
    expect(await cli.stderr).toContain(`PR maintenance stopped: Another process owns ${schedulerLock()}`);
  }, TIMEOUT_MS);

  test('stops a worker as Blocked with exit code 1', async () => {
    await holdLock(workerLock());

    const cli = launch(['--pr', 'https://github.com/a/b/pull/1']);

    expect(await cli.process.exited).toBe(1);
    expect(await cli.stderr).toContain(`PR maintenance stopped: Another process owns ${workerLock()}`);
  }, TIMEOUT_MS);

  test('is taken over by the next run once the holder dies by SIGKILL', async () => {
    const holder = await holdLock(workerLock());
    const refused = launch(['--pr', 'https://github.com/a/b/pull/1']);
    expect(await refused.process.exited).toBe(1);

    holder.kill('SIGKILL');
    await holder.exited;
    const next = launch(['--pr', 'https://github.com/a/b/pull/1'], { FAKE_GH_STATE: 'closed' });

    expect(await next.process.exited).toBe(0);
    expect(prStatus()).toBe('completed');
  }, TIMEOUT_MS);
});

describe('a worker given an interrupt', () => {
  test.each([
    ['SIGINT', 130],
    ['SIGTERM', 143],
  ] as const)('on %s records interrupted, releases its lock and exits %i', async (signal, code) => {
    const cli = launch(['--pr', 'https://github.com/a/b/pull/1']);
    await waitFor(() => prStatus() === 'running', 'the worker to run');

    cli.process.kill(signal);

    expect(await cli.process.exited).toBe(code);
    expect(prStatus()).toBe('interrupted');
    expect(await processLock(workerLock(), () => 'free')).toBe('free');
  }, TIMEOUT_MS);
});

describe('the scheduler given an interrupt', () => {
  test('stops discovery gracefully and exits 0 when it has no worker', async () => {
    const cli = launch([], { FAKE_GH_STATE: 'closed' });
    await waitFor(() => existsSync(join(stateDir, 'scheduler.json')), 'the first scan');

    cli.process.kill('SIGINT');

    expect(await cli.process.exited).toBe(0);
  }, TIMEOUT_MS);

  test('waits for its worker, which records interrupted, before exiting 0', async () => {
    const cli = launch(['--include-existing']);
    await waitFor(() => prStatus() === 'running', 'the worker to run');

    cli.process.kill('SIGINT');

    expect(await cli.process.exited).toBe(0);
    expect(prStatus()).toBe('interrupted');
    expect(await processLock(workerLock(), () => 'free')).toBe('free');
  }, TIMEOUT_MS);

  test('a second signal force-kills the remaining groups and exits immediately', async () => {
    const hungFile = join(root, 'hung.json');
    const cli = launch(['--include-existing'], { FAKE_GH_HANG: 'repos/a/b/pulls/1', FAKE_GH_HANG_FILE: hungFile });
    await waitFor(() => existsSync(hungFile), 'the worker to hang in gh');
    const hung = JSON.parse(readFileSync(hungFile, 'utf8')) as { parent: number; self: number };
    strayPids.push(hung.parent, hung.self);

    cli.process.kill('SIGINT');
    await Bun.sleep(500);
    expect(isAlive(hung.parent)).toBe(true);
    const secondSignalAt = Date.now();
    cli.process.kill('SIGINT');

    expect(await cli.process.exited).toBe(130);
    expect(Date.now() - secondSignalAt).toBeLessThan(5000);
    await waitFor(() => !isAlive(hung.parent), 'the worker group to be killed', 5000);
  }, TIMEOUT_MS);
});
