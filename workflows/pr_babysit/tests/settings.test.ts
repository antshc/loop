import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { ValueError } from '../../../src/loop/index.ts';
import { Repository, Settings } from '../settings.ts';

interface Case {
  name: string;
  result: { error: string } | { settings: ExpectedSettings };
  text: string;
}

interface ExpectedSettings {
  author: null | string;
  concurrency: number;
  copilotReview: boolean;
  interval: number;
  otelEndpoint: null | string;
  repositories: { build: string[]; name: string; path: string; test: string[] }[];
  retries: number;
  stateDir: string;
  timeout: number;
}

// Captured from the Python `Settings.load`; paths are relative to the temporary directory holding the file.
const cases = JSON.parse(readFileSync(join(import.meta.dir, 'fixtures', 'settings-cases.json'), 'utf8')) as Case[];
const messageDiffers = new Set(['bom', 'not-json']);

let root: string;
let config: string;

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-settings-')));
  mkdirSync(join(root, 'config'));
  config = join(root, 'config', 'settings.json');
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

const stripRoot = (path: string): string => (path.startsWith(root) ? path.slice(root.length) : path);

describe('settings file written for the Python version', () => {
  test.each(cases.map((entry) => [entry.name, entry] as const))('%s', (_name, entry) => {
    writeFileSync(config, entry.text, 'utf8');
    if ('error' in entry.result) {
      const { error } = entry.result;
      if (messageDiffers.has(entry.name)) expect(() => Settings.load(config)).toThrow(ValueError);
      else expect(() => Settings.load(config)).toThrow(new ValueError(error));
      return;
    }
    const { author, repositories, ...rest } = entry.result.settings;
    const settings = Settings.load(config);
    expect({
      author: settings.author,
      concurrency: settings.concurrency,
      copilotReview: settings.copilotReview,
      interval: settings.interval,
      otelEndpoint: settings.otelEndpoint,
      repositories: settings.repositories.map((repo) => ({
        build: [...repo.build],
        name: repo.name,
        path: stripRoot(repo.path),
        test: [...repo.test],
      })),
      retries: settings.retries,
      stateDir: stripRoot(settings.stateDir),
      timeout: settings.timeout,
    }).toEqual({ ...rest, author: author ?? '', repositories });
  });
});

describe('Settings', () => {
  test('the shipped example is accepted', () => {
    const settings = Settings.load(join(import.meta.dir, '..', 'settings.example.json'));
    expect(settings.repositories.map((repo) => repo.name)).toEqual(['antshc/eShop']);
    expect(settings.otelEndpoint).toBeNull();
  });

  test('a missing file is an OS error, not a settings error', () => {
    expect(() => Settings.load(join(root, 'absent.json'))).toThrow(/ENOENT/);
  });

  test('malformed structure is rejected with a settings error', () => {
    for (const text of ['[]', '{}', '{"repositories": {}}', '{"repositories": [1]}', '{"repositories": [{"name": "a/b"}]}']) {
      writeFileSync(config, text);
      expect(() => Settings.load(config)).toThrow(ValueError);
    }
  });

  test('invalid UTF-8 is a settings error', () => {
    writeFileSync(config, Buffer.from([0x7b, 0xff, 0x7d]));
    expect(() => Settings.load(config)).toThrow();
  });

  test('looks repositories up by name and rejects unconfigured ones', () => {
    const repository = new Repository('a/b', '/x', ['b'], ['t']);
    const settings = new Settings({ repositories: [repository], stateDir: '/s' });
    expect(settings.repository('a/b')).toBe(repository);
    expect(() => settings.repository('c/d')).toThrow('Repository not configured: c/d');
    expect([settings.interval, settings.concurrency, settings.retries, settings.timeout]).toEqual([60, 2, 3, 1800]);
  });
});
