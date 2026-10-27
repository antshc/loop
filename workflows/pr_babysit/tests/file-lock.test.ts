import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readdirSync, readFileSync, readlinkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  acquireFileLock,
  assertFileLockAvailable,
  FileLockContendedError,
  FileLockUnavailableError,
  loadFileLockBackend,
  withFileLock,
} from '../file-lock.ts';

interface Report {
  child?: number;
  message?: string;
  pid: number;
  status: 'acquired' | 'contended';
}

const helper = join(import.meta.dir, 'support', 'lock-process.ts');
const spawned: Bun.Subprocess<'ignore', 'pipe', 'inherit'>[] = [];
const strayChildren: number[] = [];

async function attempt(path: string): Promise<Report> {
  const process_ = start('try', path);
  const report = await firstReport(process_);
  await process_.exited;
  return report;
}

async function firstReport(process_: Bun.Subprocess<'ignore', 'pipe', 'inherit'>): Promise<Report> {
  const reader = process_.stdout.getReader();
  let text = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) throw new Error(`helper exited without a report: ${text}`);
    text += new TextDecoder().decode(value);
    if (text.includes('\n')) break;
  }
  reader.releaseLock();
  return JSON.parse(text.split('\n')[0] ?? '') as Report;
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function start(mode: 'hold' | 'try', path: string, extra?: string): Bun.Subprocess<'ignore', 'pipe', 'inherit'> {
  const process_ = Bun.spawn(['bun', helper, mode, path, ...(extra === undefined ? [] : [extra])], {
    stderr: 'inherit',
    stdin: 'ignore',
    stdout: 'pipe',
  });
  spawned.push(process_);
  return process_;
}

let directory = '';
let lockPath = '';

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'file-lock-'));
  lockPath = join(directory, 'nested', 'state.lock');
});

afterEach(async () => {
  for (const process_ of spawned.splice(0)) {
    process_.kill('SIGKILL');
    await process_.exited;
  }
  for (const pid of strayChildren.splice(0)) {
    if (isAlive(pid)) process.kill(pid, 'SIGKILL');
  }
  rmSync(directory, { force: true, recursive: true });
});

describe('file lock', () => {
  test('refuses a second acquisition from another process immediately', async () => {
    const holder = start('hold', lockPath);
    expect((await firstReport(holder)).status).toBe('acquired');

    const report = await attempt(lockPath);

    expect(report.status).toBe('contended');
    expect(report.message).toContain('Another process owns');
    expect(report.message).toContain(lockPath);
  });

  test('refuses a second acquisition in the same process while held', () => {
    const lock = acquireFileLock(lockPath);

    expect(() => acquireFileLock(lockPath)).toThrow(FileLockContendedError);
    expect(() => acquireFileLock(join(directory, 'nested', '..', 'nested', 'state.lock'))).toThrow(
      FileLockContendedError,
    );

    lock.release();
    acquireFileLock(lockPath).release();
  });

  test('contention error carries the path for the Blocked message', () => {
    const lock = acquireFileLock(lockPath);
    try {
      acquireFileLock(lockPath);
      expect.unreachable('second acquisition must be refused');
    } catch (error) {
      expect(error).toBeInstanceOf(FileLockContendedError);
      expect((error as FileLockContendedError).message).toBe(`Another process owns ${lockPath}`);
      expect((error as FileLockContendedError).path).toBe(lockPath);
    } finally {
      lock.release();
    }
  });

  test('independent paths do not contend', () => {
    using first = acquireFileLock(join(directory, 'a.lock'));
    using second = acquireFileLock(join(directory, 'b.lock'));

    expect(first.path).not.toBe(second.path);
  });

  test('another process takes the lock at once after SIGKILL of the holder', async () => {
    const holder = start('hold', lockPath);
    await firstReport(holder);
    expect((await attempt(lockPath)).status).toBe('contended');

    holder.kill('SIGKILL');
    await holder.exited;

    expect((await attempt(lockPath)).status).toBe('acquired');
  });

  test('a spawned child does not inherit the lock and survives the holder', async () => {
    const holder = start('hold', lockPath, 'spawn-child');
    const { child } = await firstReport(holder);
    expect(child).toBeNumber();
    if (child === undefined) return;
    strayChildren.push(child);

    if (process.platform === 'linux') {
      const target = readdirSync(`/proc/${String(child)}/fd`).map((fd) => {
        try {
          return readlinkSync(`/proc/${String(child)}/fd/${fd}`);
        } catch {
          return '';
        }
      });
      expect(target.some((link) => link.endsWith('state.lock'))).toBe(false);
    }

    holder.kill('SIGKILL');
    await holder.exited;

    expect(isAlive(child)).toBe(true);
    expect((await attempt(lockPath)).status).toBe('acquired');
  });

  test('keeps the lock file without a process id after release', () => {
    const lock = acquireFileLock(lockPath);
    expect(readFileSync(lockPath, 'utf8')).toBe('');

    lock.release();
    lock.release();

    expect(existsSync(lockPath)).toBe(true);
    expect(readFileSync(lockPath, 'utf8')).toBe('');
  });

  test('withFileLock holds during the action and releases on success and failure', async () => {
    const result = await withFileLock(lockPath, () => {
      expect(() => acquireFileLock(lockPath)).toThrow(FileLockContendedError);
      return Promise.resolve('done');
    });
    expect(result).toBe('done');

    const failure = await withFileLock(lockPath, () => {
      throw new Error('boom');
    }).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe('boom');

    acquireFileLock(lockPath).release();
  });
});

describe('file lock availability', () => {
  test('is available on this platform', () => {
    expect(() => {
      assertFileLockAvailable();
    }).not.toThrow();
  });

  test('reports the limitation on an unsupported platform instead of running unlocked', () => {
    expect(() => loadFileLockBackend('freebsd')).toThrow(FileLockUnavailableError);
    expect(() => loadFileLockBackend('freebsd')).toThrow('no lock implementation for platform freebsd');
  });

  test('reports a library that cannot be loaded as unavailable', () => {
    const other = process.platform === 'win32' ? 'linux' : 'win32';
    expect(() => loadFileLockBackend(other)).toThrow(FileLockUnavailableError);
  });
});
