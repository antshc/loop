import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { cpSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { compareCodePoints, encodeJson, type Json, type JsonObject } from '../../infrastructure/json.ts';
import {
  Blocked,
  processLock,
  PrState,
  readJson,
  Registry,
  schedulerLockPath,
  WORKER_CRASH_FINGERPRINT,
  writeJson,
} from '../../infrastructure/state.ts';

const fixtures = join(import.meta.dir, '..', 'fixtures');
const fixture = (name: string): string => readFileSync(join(fixtures, name), 'utf8');

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'pr-babysit-state-'));
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

function installFixture(name: string, destination: string): string {
  const path = join(root, destination);
  mkdirSync(join(path, '..'), { recursive: true });
  cpSync(join(fixtures, name), path);
  return path;
}

describe('file format', () => {
  test('uses 2-space indent, sorted keys, a trailing newline and ASCII-only text', () => {
    const path = join(root, 'out.json');
    writeJson(path, { a: 'caf\u00e9', b: [1, { a: true, z: null }], c: {}, d: [], version: 1 });
    expect(readFileSync(path, 'utf8')).toBe(
      [
        '{',
        '  "a": "caf\\u00e9",',
        '  "b": [',
        '    1,',
        '    {',
        '      "a": true,',
        '      "z": null',
        '    }',
        '  ],',
        '  "c": {},',
        '  "d": [],',
        '  "version": 1',
        '}',
        '',
      ].join('\n'),
    );
  });

  test('sorts keys by code point rather than by UTF-16 code unit', () => {
    expect(compareCodePoints('\u{1f600}', '\uffff')).toBeGreaterThan(0);
    expect(encodeJson({ '\u{1f600}': 1, '\uffff': 2 })).toBe('{\n  "\\uffff": 2,\n  "\\ud83d\\ude00": 1\n}');
  });

  test('matches the Python encoding of numbers', () => {
    const golden = fixture('numbers.json');
    expect(`${encodeJson(JSON.parse(golden) as Json)}\n`).toBe(golden);
  });

  test('writes with owner-only permissions and leaves no temporary file', () => {
    const path = join(root, 'nested', 'state.json');
    writeJson(path, { version: 1 });
    expect(readdirSync(join(root, 'nested'))).toEqual(['state.json']);
    if (process.platform !== 'win32') expect(statSync(path).mode & 0o777).toBe(0o600);
  });
});

describe('golden files written by the Python version', () => {
  test('PR state is read without migration and rewritten byte for byte', () => {
    for (const name of ['pr-state.json', 'pr-state-queued.json']) {
      const number = name === 'pr-state.json' ? 7 : 8;
      const path = installFixture(name, `prs/acme--widgets.io--${String(number)}.json`);
      const state = new PrState(root, 'acme/widgets.io', number);
      expect(state.data as JsonObject).toEqual(JSON.parse(fixture(name)) as JsonObject);
      state.save();
      expect(readFileSync(path, 'utf8')).toBe(fixture(name));
    }
  });

  test('PR state built by the port equals the Python bytes', () => {
    const state = new PrState(root, 'acme/widgets.io', 7);
    state.data.items = {
      '': { key: '', status: 'pending' },
      '\u{1f600}key': { key: 'k', status: 'superseded' },
      'thread:a': { key: 'thread:a', line: 12, path: 'src/a.py', status: 'handled' },
      'thread:Z': { body: 'caf\u00e9 \u2603 \u{1f600} \x7f tab\t quote" back\\ nl\n', key: 'thread:Z', status: 'pending' },
      '\uffffkey': { key: 'k2', status: 'pending' },
    };
    state.data.retries = { '0123': 0, deadbeef: 2 };
    state.data.copilot = { sha0: 'unavailable', sha1: 'requested' };
    state.data.batch = {
      base: 'b',
      big: 12_345_678_901_234,
      empty: [],
      flag: true,
      fraction: 0.1,
      head: 'h',
      index: 0,
      keys: ['b', 'a'],
      kind: 'ci',
      none: null,
    };
    state.status('blocked', {
      execution: 'e-1',
      fingerprint: 'worker-crash',
      head: 'abc',
      reason: 'Worker exited -9; explicit retry required',
    });
    expect(readFileSync(state.path, 'utf8')).toBe(fixture('pr-state.json'));
  });

  test('the scheduler registry is read without migration and rewritten byte for byte', () => {
    const path = installFixture('scheduler.json', 'scheduler.json');
    const registry = new Registry(root, 'octo\u00e9', ['acme/widgets.io', 'z/z', 'acme/Widgets']);
    expect(registry.data as JsonObject).toEqual(JSON.parse(fixture('scheduler.json')) as JsonObject);
    registry.enroll([], false);
    expect(readFileSync(path, 'utf8')).toBe(fixture('scheduler.json'));
  });

  test('a registry built by the port equals the Python bytes', () => {
    const registry = new Registry(root, 'octo\u00e9', ['z/z', 'acme/widgets.io', 'acme/Widgets']);
    registry.enroll(
      [
        ['acme/widgets.io', { html_url: 'https://github.com/acme/widgets.io/pull/7', number: 7 }],
        ['z/z', { html_url: 'https://github.com/z/z/pull/10', number: 10 }],
        ['acme/Widgets', { html_url: 'https://github.com/acme/Widgets/pull/2', number: 2 }],
      ],
      false,
    );
    expect(readFileSync(registry.path, 'utf8')).toBe(fixture('scheduler.json'));
  });
});

describe('interrupted writes', () => {
  const failures: [string, Parameters<typeof writeJson>[2]][] = [
    [
      'the rename',
      {
        rename: () => {
          throw new Error('interrupted');
        },
      },
    ],
    [
      'the fsync',
      {
        fsync: () => {
          throw new Error('interrupted');
        },
      },
    ],
  ];

  test.each(failures)('an interruption of %s keeps the previous file and leaves no temporary file', (_name, operations) => {
    const path = join(root, 'state.json');
    writeJson(path, { value: 'before', version: 1 });
    expect(() => {
      writeJson(path, { value: 'after', version: 1 }, operations);
    }).toThrow('interrupted');
    expect(readJson(path, {}).value).toBe('before');
    expect(readdirSync(root)).toEqual(['state.json']);
  });
});

describe('reading state', () => {
  test('missing files yield the default', () => {
    expect(readJson(join(root, 'none.json'), { version: 1 })).toEqual({ version: 1 });
  });

  test('invalid JSON blocks and is not reset', () => {
    const path = join(root, 'prs', 'a--b--1.json');
    mkdirSync(join(root, 'prs'));
    writeFileSync(path, '{');
    expect(() => new PrState(root, 'a/b', 1)).toThrow(Blocked);
    expect(() => new PrState(root, 'a/b', 1)).toThrow('Cannot read state');
    expect(readFileSync(path, 'utf8')).toBe('{');
  });

  test('invalid UTF-8 blocks instead of being replaced', () => {
    const path = join(root, 'state.json');
    writeFileSync(path, Buffer.from([0x7b, 0x22, 0x61, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d]));
    expect(() => readJson(path, {})).toThrow('Cannot read state');
  });

  test.each([['[]'], ['{"version": 2}'], ['{}'], ['"x"']])('schema %s is rejected', (text) => {
    const path = join(root, 'state.json');
    writeFileSync(path, text);
    expect(() => readJson(path, {})).toThrow(`Invalid state schema: ${path}`);
  });

  test('a state file for another PR is an identity mismatch', () => {
    installFixture('pr-state-queued.json', 'prs/acme--widgets.io--9.json');
    expect(() => new PrState(root, 'acme/widgets.io', 9)).toThrow('State identity mismatch');
  });

  test.each([
    ['unknown status', { status: 'weird' }],
    ['items not an object', { items: [] }],
    ['batch not an object', { batch: 1 }],
    ['item without status', { items: { a: { key: 'a' } } }],
    ['item with unknown status', { items: { a: { status: 'done' } } }],
  ])('%s is invalid PR state', (_name, patch) => {
    const path = join(root, 'prs', 'a--b--1.json');
    writeJson(path, { batch: null, copilot: {}, items: {}, number: 1, repo: 'a/b', retries: {}, status: 'queued', version: 1, ...patch });
    expect(() => new PrState(root, 'a/b', 1)).toThrow(`Invalid PR state: ${path}`);
  });

  test('a new PR starts queued with empty work and is not written until saved', () => {
    const state = new PrState(root, 'a/b', 1);
    expect(state.data).toEqual({
      batch: null,
      copilot: {},
      items: {},
      number: 1,
      repo: 'a/b',
      retries: {},
      status: 'queued',
      version: 1,
    });
    expect(state.path).toBe(join(root, 'prs', 'a--b--1.json'));
    expect(state.lockPath).toBe(join(root, 'prs', 'a--b--1.lock'));
    expect(readdirSync(root)).toEqual([]);
  });
});

describe('worker-crash', () => {
  test('stays blocked, across reloads, until an explicit retry resumes the PR', () => {
    const state = new PrState(root, 'a/b', 1);
    state.status('running', { execution: 'e', reason: '' });
    state.markWorkerCrash(-9);
    expect(state.data.status).toBe('blocked');
    expect(state.data.fingerprint).toBe(WORKER_CRASH_FINGERPRINT);
    expect(state.data.reason).toBe('Worker exited -9; explicit retry required');

    const reloaded = new PrState(root, 'a/b', 1);
    expect(reloaded.awaitsExplicitRetry).toBe(true);
    reloaded.status('queued');
    expect(new PrState(root, 'a/b', 1).awaitsExplicitRetry).toBe(false);
  });

  test('an ordinary blocked PR does not await an explicit retry', () => {
    const state = new PrState(root, 'a/b', 1);
    state.status('blocked', { fingerprint: 'abc', reason: 'x' });
    expect(state.awaitsExplicitRetry).toBe(false);
  });
});

describe('registry', () => {
  const pr = (number: number): [string, { html_url: string; number: number }] => [
    'a/b',
    { html_url: `https://github.com/a/b/pull/${String(number)}`, number },
  ];

  test('the first scan baselines existing PRs as ignored and later discoveries queue', () => {
    const registry = new Registry(root, 'me', ['a/b']);
    registry.enroll([pr(1)], false);
    expect(new PrState(root, 'a/b', 1).data.status).toBe('ignored');
    expect(registry.data.initialized).toBe(true);
    registry.enroll([pr(1), pr(2)], false);
    expect(new PrState(root, 'a/b', 1).data.status).toBe('ignored');
    expect(new PrState(root, 'a/b', 2).data.status).toBe('queued');
    expect(Object.keys(new Registry(root, 'me', ['a/b']).data.prs)).toEqual(['a/b#1', 'a/b#2']);
  });

  test('include-existing queues the baseline', () => {
    new Registry(root, 'me', ['a/b']).enroll([pr(1)], true);
    expect(new PrState(root, 'a/b', 1).data.status).toBe('queued');
  });

  test('replay repairs a crash between PR state and registry publication without resetting state', () => {
    new PrState(root, 'a/b', 1).status('blocked', { fingerprint: 'f' });
    new Registry(root, 'me', ['a/b']).enroll([pr(1)], true);
    expect(new PrState(root, 'a/b', 1).data.status).toBe('blocked');
  });

  test('a changed author or repository set blocks', () => {
    new Registry(root, 'me', ['a/b', 'c/d']).enroll([], false);
    const message = 'Discovery configuration changed; choose a new state_dir or migrate its baseline';
    expect(() => new Registry(root, 'you', ['a/b', 'c/d'])).toThrow(message);
    expect(() => new Registry(root, 'me', ['a/b'])).toThrow(message);
    expect(() => new Registry(root, 'me', ['d/c', 'a/b'])).toThrow(message);
    expect(new Registry(root, 'me', ['c/d', 'a/b']).data.initialized).toBe(true);
  });

  test('a registry without a PR map is invalid', () => {
    writeJson(join(root, 'scheduler.json'), { author: 'me', prs: [], repositories: ['a/b'], version: 1 });
    expect(() => new Registry(root, 'me', ['a/b'])).toThrow('Invalid scheduler registry');
  });
});

describe('process lock', () => {
  test('excludes a duplicate owner as Blocked and releases afterwards', async () => {
    const path = join(root, 'worker.lock');
    const contended = await processLock(path, () => processLock(path, () => 'duplicate').catch((error: unknown) => error));
    expect(contended).toBeInstanceOf(Blocked);
    expect((contended as Blocked).message).toBe(`Another process owns ${path}`);
    expect(await processLock(path, () => 'again')).toBe('again');
    expect(schedulerLockPath(root)).toBe(join(root, 'scheduler.lock'));
  });
});
