import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { Settings } from '../settings.ts';

import { FileLockUnavailableError } from '../file-lock.ts';
import { Blocked } from '../infrastructure/state.ts';
import { main, type MainDeps, type ServeOptions } from '../main.ts';

interface Calls {
  errors: string[];
  runs: [string, number][];
  serves: { config: string; options: ServeOptions }[];
  stderr: string;
  stdout: string;
}

let root: string;
let config: string;

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-main-')));
  config = join(root, 'settings.json');
  writeFileSync(
    config,
    JSON.stringify({ repositories: [{ build: ['b'], name: 'a/b', path: 'x', test: ['t'] }] }),
  );
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

function harness(overrides: Partial<MainDeps> = {}): { calls: Calls; deps: Partial<MainDeps> } {
  const calls: Calls = { errors: [], runs: [], serves: [], stderr: '', stdout: '' };
  const deps: Partial<MainDeps> = {
    error: (message) => calls.errors.push(message),
    run: (_settings: Settings, repo, number) => {
      calls.runs.push([repo, number]);
      return Promise.resolve(0);
    },
    serve: (_settings: Settings, path, options) => {
      calls.serves.push({ config: path, options });
      return Promise.resolve(0);
    },
    stderr: (text) => (calls.stderr += text),
    stdout: (text) => (calls.stdout += text),
    ...overrides,
  };
  return { calls, deps };
}

describe('bad input', () => {
  const PR = 'https://github.com/o/r/pull/1';
  test.each([
    ['no arguments', []],
    ['missing --config', ['--pr', PR]],
    ['unknown flag', ['--config', 'c', '--nope']],
    ['stray positional', ['--config', 'c', 'extra']],
    ['--config without a value', ['--config']],
    ['--pr without a value', ['--config', 'c', '--pr']],
    ['--retry without a value', ['--config', 'c', '--retry']],
    ['a --pr that is not a PR URL', ['--config', 'c', '--pr', 'https://example.com/o/r/pull/1']],
    ['a --pr with a trailing path', ['--config', 'c', '--pr', `${PR}/files`]],
    ['--pr with --include-existing', ['--config', 'c', '--pr', PR, '--include-existing']],
    ['--pr with --retry', ['--config', 'c', '--pr', PR, '--retry', PR]],
  ])('%s prints usage and exits 2 without running anything', async (_name, argv) => {
    const { calls, deps } = harness();
    expect(await main(argv, deps)).toBe(2);
    expect(calls.stderr).toStartWith('Usage: pr_babysit [options]');
    expect(calls.stderr).toContain('error:');
    expect(calls.runs).toEqual([]);
    expect(calls.serves).toEqual([]);
    expect(calls.errors).toEqual([]);
  });

  test('names the missing required option', async () => {
    const { calls, deps } = harness();
    await main([], deps);
    expect(calls.stderr).toContain("required option '--config <path>' not specified");
  });

  test('explains a bad PR URL', async () => {
    const { calls, deps } = harness();
    await main(['--config', config, '--pr', 'nope'], deps);
    expect(calls.stderr).toContain('error: --pr must be a GitHub PR URL');
  });
});

describe('help', () => {
  test('is generated from the flags and exits 0', async () => {
    const { calls, deps } = harness();
    expect(await main(['--help'], deps)).toBe(0);
    for (const flag of ['--config <path>', '--pr <url>', '--include-existing', '--retry <url>', '--help']) {
      expect(calls.stdout).toContain(flag);
    }
    expect(calls.stdout).toContain('Discover PRs continuously or babysit one PR');
    expect(calls.stdout).toContain('Retry an enrolled blocked PR during discovery');
  });
});

describe('dispatch', () => {
  test('--pr maintains that one PR', async () => {
    const { calls, deps } = harness();
    expect(await main(['--config', config, '--pr', 'https://github.com/o.x/r-y/pull/123/'], deps)).toBe(0);
    expect(calls.runs).toEqual([['o.x/r-y', 123]]);
    expect(calls.serves).toEqual([]);
  });

  test('without --pr discovery starts with the resolved config and options', async () => {
    const { calls, deps } = harness();
    expect(await main(['--config', config], deps)).toBe(0);
    expect(calls.serves).toEqual([{ config, options: { includeExisting: false, retry: undefined } }]);
    await main(['--config', config, '--include-existing', '--retry', 'https://github.com/a/b/pull/2'], deps);
    expect(calls.serves[1]?.options).toEqual({ includeExisting: true, retry: 'https://github.com/a/b/pull/2' });
  });

  test('passes the loaded settings through', async () => {
    let seen: Settings | undefined;
    const { deps } = harness({
      serve: (settings) => {
        seen = settings;
        return Promise.resolve(0);
      },
    });
    await main(['--config', config], deps);
    expect(seen?.repositories.map((repo) => repo.name)).toEqual(['a/b']);
  });

  test('returns the exit code of the run', async () => {
    const { deps } = harness({ run: () => Promise.resolve(7) });
    expect(await main(['--config', config, '--pr', 'https://github.com/o/r/pull/1'], deps)).toBe(7);
  });
});

describe('failures', () => {
  test('invalid settings, unreadable files and blocked runs exit 1 with the message', async () => {
    const bad = join(root, 'bad.json');
    writeFileSync(bad, '{"repositories": []}');
    const { calls, deps } = harness({
      serve: () => Promise.reject(new Blocked('Another process owns x')),
    });
    expect(await main(['--config', bad], deps)).toBe(1);
    expect(await main(['--config', join(root, 'absent.json')], deps)).toBe(1);
    expect(await main(['--config', config], deps)).toBe(1);
    expect(calls.errors).toHaveLength(3);
    expect(calls.errors[0]).toBe('configure at least one repository, without duplicates');
    expect(calls.errors[1]).toContain('ENOENT');
    expect(calls.errors[2]).toBe('Another process owns x');
  });

  test('unexpected errors propagate', async () => {
    const { deps } = harness({ serve: () => Promise.reject(new Error('boom')) });
    const failure = await main(['--config', config], deps).catch((error: unknown) => error);
    expect(failure).toEqual(new Error('boom'));
  });
});

describe('startup without OS locking', () => {
  test('reports the limitation and exits 1 instead of running unlocked', async () => {
    const { calls, deps } = harness({
      run: () => Promise.reject(new FileLockUnavailableError('no lock implementation for platform freebsd')),
    });

    expect(await main(['--config', config, '--pr', 'https://github.com/o/r/pull/1'], deps)).toBe(1);

    expect(calls.errors).toEqual(['File locking is unavailable: no lock implementation for platform freebsd']);
  });
});
