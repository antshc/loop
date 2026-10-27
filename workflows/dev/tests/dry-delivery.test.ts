import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

import { Role } from '../../../src/loop/index.ts';
import { type DevDependencies, main } from '../main.ts';
import { testDirectory } from './support/harness.ts';
import { RecordingObserver, silenceLogging } from './support/observer.ts';
import { Stream } from './support/stream.ts';

let directory: string;

beforeEach(() => {
  directory = testDirectory('dev-dry-delivery-');
});

afterEach(() => {
  silenceLogging();
  rmSync(directory, { force: true, recursive: true });
});

type PhaseRecords = Record<string, { exitCode: number; lines: string[] }>;

/** What the Python Workflow reported for the same dry runs; its review and readiness phases belong to #141. */
const PYTHON = JSON.parse(readFileSync(join(import.meta.dir, 'fixtures', 'phase-records.json'), 'utf8')) as PhaseRecords;


/** A console record without its timestamp and duration. */
const normalized = (line: string): string => line.replace(/^\S+ \S+ /, '').replace(/ · \d+\.\ds/, '');

async function dry(...extra: string[]): Promise<{ code: number; observer: RecordingObserver; records: string[] }> {
  const observers: RecordingObserver[] = [];
  const stderr = new Stream();
  const dependencies: DevDependencies = {
    clock: () => 0,
    cwd: () => directory,
    env: {},
    newObserver: (endpoint) => {
      const observer = new RecordingObserver(endpoint);
      observers.push(observer);
      return observer;
    },
    stderr,
    stdout: new Stream(),
  };
  const code = await main(['--dry', '--fake-speed', '0', '--log-dir', directory, ...extra], dependencies);
  const [observer] = observers;
  if (observer === undefined) {
    throw new Error('no observer was built');
  }
  return { code, observer, records: stderr.text.split('\n').filter((line) => line !== '').map(normalized) };
}

const developerRuns = (observer: RecordingObserver): string[] =>
  observer.runs.filter((run) => run.agent === Role.DEVELOPER).map((run) => run.output ?? '');

describe('a dry run delivers the fake Spec end to end', () => {
  test('delivers every Ticket in order and publishes the runs to the default endpoint', async () => {
    const { code, observer } = await dry();

    const names = observer.spans.map((span) => span.name);
    expect(code).toBe(0);
    expect(observer.endpoint).toBe('http://localhost:4318');
    expect(observer.worktrees).toEqual(['ready', 'removing', 'removed']);
    expect(names.slice(0, 2)).toEqual(['dev', 'Spec']);
    expect(names.filter((name) => name === 'Implement ticket')).toHaveLength(3);
    expect(names.indexOf('Refine changes')).toBeGreaterThan(names.indexOf('Deliver tickets'));
    expect(names.indexOf('Close ticket')).toBeLessThan(names.indexOf('Refine changes'));
  });

  test('exports the workflow spans as the shared roles', async () => {
    const { observer } = await dry();

    const roles = new Map(observer.spans.map((span) => [span.name, span.role]));
    expect(roles.get('Spec')).toBe(Role.ENGINEER);
    expect(['Validate ticket commit', 'Close ticket', 'Publish draft PR'].map((name) => roles.get(name))).toEqual([
      Role.CODE,
      Role.CODE,
      Role.CODE,
    ]);
    expect(roles.get('Implement ticket')).toBe(Role.DEVELOPER);
  });

  test('reworks the second Ticket after a failed first attempt', async () => {
    const { code, observer } = await dry();

    const outputs = developerRuns(observer);
    expect(code).toBe(0);
    expect(outputs).toHaveLength(3);
    expect(outputs[1]).toContain('"status": "failed"');
    expect(outputs[2]).toContain('"status": "completed"');
  });

  test('reports usage and tools like a real CLI', async () => {
    const { observer } = await dry();

    const record = observer.runs[0];
    expect((record?.usage?.totalInputTokens ?? 0) > 0).toBe(true);
    expect((record?.usage?.totalAiu ?? 0) > 0).toBe(true);
    expect(record?.tools.map((tool) => tool.name)).toEqual(['view', 'edit', 'bash']);
    expect(record?.exitCode).toBe(0);
    expect(record?.nativeId).not.toBeNull();
    expect(new Set(observer.runs.map((run) => run.agent))).toEqual(new Set(['chore', Role.DEVELOPER, Role.REVIEWER]));
  });

  test('with --no-rework runs each Ticket once', async () => {
    const { code, observer } = await dry('--no-rework');

    expect(code).toBe(0);
    expect(developerRuns(observer)).toHaveLength(2);
  });

  test('with an explicit endpoint publishes there instead of the default', async () => {
    const { observer } = await dry('--otlp-endpoint', 'http://collector:4318');

    expect(observer.endpoint).toBe('http://collector:4318');
  });

  test.each([
    ['dry', []],
    ['dryNoRework', ['--no-rework']],
  ])('reports the same phases and statuses as the Python Workflow (%s)', async (name, flags) => {
    const { code, records } = await dry(...flags);

    const expected = PYTHON[name];
    expect(code).toBe(expected?.exitCode ?? -1);
    expect(records).toEqual(expected?.lines ?? []);
  });
});
