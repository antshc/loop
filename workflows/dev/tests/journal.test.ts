import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { compactJson, ExecutionStoreError, FileExecutionStore } from '../journal.ts';

const GOLDEN = join(import.meta.dir, 'fixtures', 'execution-log-2026-01-02.json');
const TITLE = 'Zażółć gęślą "jaźń" \\ \u007f \u2603';
const TICKET_10 = 'https://github.com/antshc/loop/issues/10';
const TICKET_11 = 'https://github.com/antshc/loop/issues/11';

let directory: string;

beforeEach(() => {
  directory = realpathSync(mkdtempSync(join(tmpdir(), 'dev-journal-')));
});

afterEach(() => {
  rmSync(directory, { force: true, recursive: true });
});

async function fail(target: FileExecutionStore, key = 't1'): Promise<void> {
  await target.recordFailure(key, { items: [1], owner: 'o', repo: 'r', taskId: '1', title: 'T' });
}

function store(clock: () => Date = () => new Date(Date.UTC(2026, 0, 1))): FileExecutionStore {
  return new FileExecutionStore(directory, { clock });
}

describe('the dev execution log encoding', () => {
  test('matches the log the Python Workflow wrote, byte for byte', async () => {
    let now = new Date(Date.UTC(2026, 0, 2, 3, 4, 5));
    const target = store(() => now);
    await target.recordFailure(TICKET_10, { items: [10], owner: 'antshc', repo: 'loop', taskId: '10', title: TITLE });
    await target.recordFailure(TICKET_11, {
      items: [11, 12],
      owner: 'antshc',
      repo: 'loop',
      taskId: '11',
      title: 'Apply discount at checkout',
    });
    now = new Date(Date.UTC(2026, 0, 2, 23, 59, 59));
    await target.recordFailure(TICKET_10, { items: [10, 13], owner: 'antshc', repo: 'loop', taskId: '10', title: TITLE });

    expect(readFileSync(join(directory, 'dev-execution-log-2026-01-02.json'), 'utf8')).toBe(readFileSync(GOLDEN, 'utf8'));
  });

  test('reads the log the Python Workflow wrote', async () => {
    writeFileSync(join(directory, 'dev-execution-log-2026-01-02.json'), readFileSync(GOLDEN));
    const target = store(() => new Date(Date.UTC(2026, 0, 2, 12)));

    expect([await target.failedAttempts(TICKET_10), await target.failedAttempts(TICKET_11)]).toEqual([2, 1]);
  });

  test('is compact, keeps its key order and escapes non-ASCII text', async () => {
    await fail(store());

    expect(readFileSync(join(directory, 'dev-execution-log-2026-01-01.json'), 'utf8')).toBe(
      '[{"owner":"o","repo":"r","type":"spec","task_id":"1","title":"T","task":"t1","count":1,' +
        '"last_run":"2026-01-01T00:00:00Z","last_items":[1]}]',
    );
    expect(compactJson(['é', '\u007f', '"', '\n'])).toBe('["\\u00e9","\\u007f","\\"","\\n"]');
  });

  test('differs from the PR state encoding, which is indented and has its keys sorted', async () => {
    await fail(store());
    const text = readFileSync(join(directory, 'dev-execution-log-2026-01-01.json'), 'utf8');
    const keys = Object.keys((JSON.parse(text) as Record<string, unknown>[])[0] ?? {});

    expect(text).not.toContain('\n');
    expect(text).not.toContain('  ');
    expect(keys).not.toEqual([...keys].sort());
  });
});

describe('the execution store', () => {
  test('counts failures per key and resets one key', async () => {
    const target = store();

    await fail(target);
    await fail(target);
    await fail(target, 't2');

    expect([await target.failedAttempts('t1'), await target.failedAttempts('t2')]).toEqual([2, 1]);
    await target.reset('t1');
    expect([await target.failedAttempts('t1'), await target.failedAttempts('t2')]).toEqual([0, 1]);
  });

  test('keeps one log file per UTC day', async () => {
    await fail(store(() => new Date(Date.UTC(2026, 0, 1, 23, 59, 59))));

    expect(await store(() => new Date(Date.UTC(2026, 0, 2))).failedAttempts('t1')).toBe(0);
    expect(readdirSync(directory)).toEqual(['dev-execution-log-2026-01-01.json']);
  });

  test('creates its directory on the first failure', async () => {
    const nested = new FileExecutionStore(join(directory, 'a', 'b'), { clock: () => new Date(Date.UTC(2026, 0, 1)) });

    await fail(nested);

    expect(readdirSync(join(directory, 'a', 'b'))).toEqual(['dev-execution-log-2026-01-01.json']);
  });

  test.each(['{not json', '{"a": 1}'])('rejects the corrupt log %p', async (content) => {
    writeFileSync(join(directory, 'dev-execution-log-2026-01-01.json'), content);

    const error = await store().failedAttempts('t1').then(
      () => null,
      (failure: unknown) => failure,
    );

    expect(error).toBeInstanceOf(ExecutionStoreError);
  });
});
