import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { Cancelled, exitCodeFor } from '../../src/loop/run/cancelled.ts';
import { runEntrypoint } from '../../src/loop/run/entrypoint.ts';
import {
  createProcessRunner,
  ProcessFailedError,
  processRunner,
  ProcessTimeoutError,
} from '../../src/loop/run/process.ts';
import { groupAlive, ProcessSupervisor, type SignalSource } from '../../src/loop/run/supervisor.ts';

let directory = '';

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'loop-process-'));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

function shell(script: string): string[] {
  return ['sh', '-c', script];
}

describe('process helper', () => {
  test('returns exit code and decoded output without raising when check is off', async () => {
    const result = await processRunner.run(shell('echo out; echo err >&2; exit 3'));

    expect(result.exitCode).toBe(3);
    expect(result.stdout).toBe('out\n');
    expect(result.stderr).toBe('err\n');
  });

  test('raises an error carrying exit status and captured output when check is on and the child fails', async () => {
    const failure = await processRunner
      .run(shell('echo out; echo err >&2; exit 3'), { check: true })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ProcessFailedError);
    const error = failure as ProcessFailedError;
    expect([error.exitCode, error.stdout, error.stderr]).toEqual([3, 'out\n', 'err\n']);
  });

  test('replaces invalid UTF-8 in captured output with U+FFFD instead of raising', async () => {
    const result = await processRunner.run(shell(String.raw`printf '\377ok\376'`), { check: true });

    expect(result.stdout).toBe('\uFFFDok\uFFFD');
  });

  test('merges the given environment over the parent environment', async () => {
    const result = await processRunner.run(shell('echo "$LOOP_PROBE:${PATH:+path}"'), {
      env: { LOOP_PROBE: 'value' },
    });

    expect(result.stdout).toBe('value:path\n');
  });

  test('runs the child in the given working directory', async () => {
    const result = await processRunner.run(['pwd', '-P'], { cwd: directory });

    expect(result.stdout.trim().endsWith(directory.split('/').pop() ?? '?')).toBe(true);
  });

  test('kills the whole process group and raises a timeout error when the timeout elapses', async () => {
    const pidFile = join(directory, 'pids');
    const failure = await processRunner
      .run(shell(`sleep 300 & echo $! > '${pidFile}'; wait`), { timeoutSec: 0.3 })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ProcessTimeoutError);
    const grandchild = Number(await Bun.file(pidFile).text());
    expect(isAlive(grandchild)).toBe(false);
  });

  test('lets a fire-and-forget child be polled and waited for', async () => {
    const handle = processRunner.start(shell('sleep 0.2; echo done'));

    expect(handle.poll()).toBeNull();
    const result = await handle.wait();
    expect(result.stdout).toBe('done\n');
    expect(handle.poll()).toBe(0);
  });

  test('terminates a fire-and-forget child that ignores SIGTERM with SIGKILL after the grace period', async () => {
    const runner = createProcessRunner(new ProcessSupervisor({ graceMs: 200 }));
    const handle = runner.start(shell(`trap '' TERM; echo ready; while :; do sleep 1; done`));
    await Bun.sleep(150);

    handle.terminate();
    const result = await handle.wait();

    expect(result.signal).toBe('SIGKILL');
    expect(groupAlive(handle.pid)).toBe(false);
  });

  test('terminating twice or after the group is gone is safe', async () => {
    const supervisor = new ProcessSupervisor({ graceMs: 200 });
    const handle = createProcessRunner(supervisor).start(['sleep', '300']);

    const first = supervisor.terminate(handle.pid);
    const second = supervisor.terminate(handle.pid);
    await Promise.all([first, second, handle.wait()]);

    await supervisor.terminate(handle.pid);
    expect(groupAlive(handle.pid)).toBe(false);
  });
});

describe('signal listeners', () => {
  test('are absent when no child runs and only present while one does', async () => {
    const before = listenerCount();
    const handle = processRunner.start(shell('sleep 0.2'));

    expect(listenerCount()).toBe(before + 2);
    await handle.wait();

    expect(listenerCount()).toBe(before);
  });

  test('are reference counted across concurrent children', async () => {
    const fake = new FakeSignals();
    const runner = createProcessRunner(new ProcessSupervisor({ signals: fake }));
    const quick = runner.start(shell('exit 0'));
    const slow = runner.start(shell('sleep 0.3'));

    await quick.wait();
    expect(fake.count()).toBe(2);
    await slow.wait();
    expect(fake.count()).toBe(0);
  });

  test('keep a force-exit listener after the first signal and force-kill on the second', async () => {
    const fake = new FakeSignals();
    const exits: number[] = [];
    const supervisor = new ProcessSupervisor({ exit: (code) => exits.push(code), graceMs: 200, signals: fake });
    const handle = createProcessRunner(supervisor).start(shell(`trap '' TERM; echo ready; while :; do sleep 1; done`));
    await Bun.sleep(150);

    fake.emit('SIGINT');
    const cancelled = await handle.wait().catch((error: unknown) => error);

    expect(cancelled).toBeInstanceOf(Cancelled);
    expect((cancelled as Cancelled).exitCode).toBe(130);
    expect(groupAlive(handle.pid)).toBe(false);
    expect(fake.count()).toBe(2);

    fake.emit('SIGTERM');
    expect(exits).toEqual([exitCodeFor('SIGTERM')]);
  });

  test('still allow new children after a cancellation so cleanup can run commands', async () => {
    const fake = new FakeSignals();
    const runner = createProcessRunner(new ProcessSupervisor({ graceMs: 200, signals: fake }));
    const handle = runner.start(['sleep', '300']);

    fake.emit('SIGTERM');
    await handle.wait().catch(() => undefined);

    const result = await runner.run(['echo', 'cleanup']);
    expect(result.stdout).toBe('cleanup\n');
  });
});

describe('entrypoint', () => {
  test('maps Cancelled to its exit code', async () => {
    const exits: number[] = [];

    await runEntrypoint(
      () => Promise.reject(new Cancelled('SIGTERM')),
      { exit: (code) => exits.push(code) },
    );

    expect(exits).toEqual([143]);
  });

  test('rethrows other errors', async () => {
    const run = runEntrypoint(() => Promise.reject(new Error('boom')), { exit: () => undefined });

    const failure = await run.catch((error: unknown) => error);

    expect(failure).toEqual(new Error('boom'));
  });
});

class FakeSignals implements SignalSource {
  readonly #listeners = new Map<string, Set<() => void>>();

  count(): number {
    return [...this.#listeners.values()].reduce((total, set) => total + set.size, 0);
  }

  emit(signal: string): void {
    for (const listener of [...(this.#listeners.get(signal) ?? [])]) {
      listener();
    }
  }

  off(signal: string, listener: () => void): void {
    this.#listeners.get(signal)?.delete(listener);
  }

  on(signal: string, listener: () => void): void {
    const set = this.#listeners.get(signal) ?? new Set();
    set.add(listener);
    this.#listeners.set(signal, set);
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

function listenerCount(): number {
  return process.listenerCount('SIGINT') + process.listenerCount('SIGTERM');
}
