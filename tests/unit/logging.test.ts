import { afterEach, describe, expect, spyOn, test } from 'bun:test';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { LogExtra, LogLevel, LogRecord } from '../../src/loop/index.ts';

import { configureLogging, getLogger, jsonLine, setLogSink } from '../../src/loop/diagnostics.ts';
import { golden } from './support/fixtures.ts';

interface GoldenRecord {
  extra: LogExtra;
  level: LogLevel;
  line: string;
  logger: string;
  message: string;
}

const directories: string[] = [];

class Console {
  text = '';

  write(chunk: string): boolean {
    this.text += chunk;
    return true;
  }
}

afterEach(() => {
  setLogSink(null);
  for (const directory of directories.splice(0)) {
    rmSync(directory, { force: true, recursive: true });
  }
});

function directory(): string {
  const created = mkdtempSync(join(tmpdir(), 'loop-logging-'));
  directories.push(created);
  return created;
}

function emitInfoAndDebug(): void {
  const logger = getLogger('loop.test');
  logger.debug('detail');
  logger.info('summary');
}

function records(file: string): Record<string, unknown>[] {
  return readFileSync(file, 'utf8')
    .split('\n')
    .filter((line) => line !== '')
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

describe('silent until configured', () => {
  test('library code writes nothing when no logger is configured', () => {
    const stderr = spyOn(process.stderr, 'write');
    const stdout = spyOn(process.stdout, 'write');
    try {
      const logger = getLogger('loop.git.git_cli');
      logger.debug('d');
      logger.info('i');
      logger.warning('w');
      logger.error('e', { extra: 1 });

      expect(stderr).not.toHaveBeenCalled();
      expect(stdout).not.toHaveBeenCalled();
    } finally {
      stderr.mockRestore();
      stdout.mockRestore();
    }
  });

  test('removing the sink restores silence', () => {
    const console_ = new Console();
    configureLogging({ logFile: null, stream: console_ });
    setLogSink(null);

    getLogger('loop.test').error('boom');

    expect(console_.text).toBe('');
  });

  test('a failing sink never fails the caller', () => {
    setLogSink(() => {
      throw new Error('disk full');
    });

    expect(() => {
      getLogger('loop.test').info('still fine');
    }).not.toThrow();
  });
});

describe('record shape', () => {
  test('matches the records the Python JSON formatter emits, with no level, time or name fields', async () => {
    const fixture = await golden<GoldenRecord[]>('log-records.json');
    const seen: LogRecord[] = [];
    setLogSink((record) => seen.push(record));

    for (const { extra, level, logger, message } of fixture) {
      getLogger(logger)[level](message, extra);
    }

    expect(seen.map((record) => jsonLine(record))).toEqual(fixture.map(({ line }) => line));
    for (const record of seen) {
      expect(Object.keys(JSON.parse(jsonLine(record)) as object)).not.toContainAnyValues(['level', 'time', 'name']);
    }
  });

  test('the file holds one {"message", ...extras} object per line', () => {
    const file = join(directory(), 'loop.log');
    configureLogging({ level: 'DEBUG', logFile: file, stream: new Console() });

    getLogger('loop.test').info('started', { attempt: 2 });
    getLogger('loop.test').debug('plain');

    expect(records(file)).toEqual([{ attempt: 2, message: 'started' }, { message: 'plain' }]);
  });
});

describe('configuration', () => {
  test('info logs to the console only', () => {
    const file = join(directory(), 'loop.log');
    const console_ = new Console();

    configureLogging({ level: 'INFO', logFile: file, stream: console_ });
    emitInfoAndDebug();

    expect(console_.text).toContain('summary');
    expect(console_.text).not.toContain('detail');
    expect(existsSync(file)).toBe(false);
  });

  test('console lines read time, level, logger name and message', () => {
    const console_ = new Console();

    configureLogging({ logFile: null, stream: console_ });
    getLogger('loop.test').warning('careful');

    expect(console_.text).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING loop\.test: careful\n$/);
  });

  test('debug logs info and debug to the console and the JSON file, creating its folder', () => {
    const file = join(directory(), 'nested', 'loop.log');
    const console_ = new Console();

    configureLogging({ level: 'DEBUG', logFile: file, stream: console_ });
    emitInfoAndDebug();

    expect(console_.text).toContain('summary');
    expect(console_.text).toContain('detail');
    expect(records(file).map((record) => record.message)).toEqual(['detail', 'summary']);
  });

  test('debug without a log file writes no file', () => {
    const folder = directory();
    const console_ = new Console();
    const previous = process.cwd();
    process.chdir(folder);
    try {
      configureLogging({ level: 'DEBUG', logFile: null, stream: console_ });
      emitInfoAndDebug();
    } finally {
      process.chdir(previous);
    }

    expect(console_.text).toContain('detail');
    expect(readdirSync(folder)).toEqual([]);
  });

  test('an unknown level falls back to info', () => {
    const console_ = new Console();

    configureLogging({ level: 'LOUD', logFile: join(directory(), 'loop.log'), stream: console_ });
    emitInfoAndDebug();

    expect(console_.text).toContain('summary');
    expect(console_.text).not.toContain('detail');
  });

  test('repeated configuration does not duplicate output', () => {
    const console_ = new Console();

    configureLogging({ logFile: null, stream: new Console() });
    configureLogging({ logFile: null, stream: console_ });
    emitInfoAndDebug();

    expect(console_.text.split('summary')).toHaveLength(2);
  });

  test('the default scope leaves loggers outside the loop subtree alone', () => {
    const console_ = new Console();

    configureLogging({ level: 'DEBUG', logFile: null, stream: console_ });
    getLogger('workflow.other').warning('app record');
    getLogger('looping').warning('lookalike');

    expect(console_.text).toBe('');
  });

  test('an empty logger name covers the whole application', () => {
    const console_ = new Console();

    configureLogging({ logFile: null, logger: '', stream: console_ });
    getLogger('workflow.other').info('app record');

    expect(console_.text).toContain('app record');
  });
});
