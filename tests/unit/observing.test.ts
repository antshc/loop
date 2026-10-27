import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

import { copilotSessionId } from '../../src/loop/clis/copilot-log.ts';
import {
  AgentBuilder,
  AgentContext,
  AgentProfile,
  AgentRequest,
  type AgentRunRecord,
  CliOutcome,
  type CliRunner,
  RunContext as Context,
  copilot,
  CopilotLogReader,
  DockerRuntime,
  NativeHandle,
  ObservingCliRunner,
  ProcessFailedError,
  Resume,
  type RunObserver,
  SessionName,
  Start,
  ValueError,
  type WorktreeEvent,
} from '../../src/loop/index.ts';
import { RecordingRunner, rejectionOf } from './support/doubles.ts';
import { FIXTURES } from './support/fixtures.ts';

const NAME = new SessionName('ticket-7');
const PROFILE = new AgentProfile(copilot, { model: 'claude-haiku-4.5' });

class RecordingObserver implements RunObserver {
  readonly records: AgentRunRecord[] = [];
  readonly worktrees: WorktreeEvent[] = [];

  agentRun(record: AgentRunRecord): void {
    this.records.push(record);
  }

  worktree(event: WorktreeEvent): void {
    this.worktrees.push(event);
  }
}

const brokenObserver: RunObserver = {
  agentRun: () => Promise.reject(new Error('sink down')),
  worktree: () => Promise.reject(new Error('sink down')),
};

class FakeRunner implements CliRunner {
  readonly profiles: AgentProfile[] = [];

  constructor(private readonly error: Error | null = null) {}

  run(profile: AgentProfile): Promise<CliOutcome> {
    this.profiles.push(profile);
    return this.error === null
      ? Promise.resolve(new CliOutcome('ok', new NativeHandle('copilot', NAME.value), 0))
      : Promise.reject(this.error);
  }
}

let home = '';

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'loop-observing-'));
});

afterEach(() => {
  rmSync(home, { force: true, recursive: true });
});

function installLog(name: SessionName, fixture = 'first-turn'): string {
  const path = join(home, 'session-state', copilotSessionId(name), 'events.jsonl');
  mkdirSync(dirname(path), { recursive: true });
  copyFileSync(join(FIXTURES, 'copilot-log', `${fixture}.jsonl`), path);
  return path;
}

function observed(runner: CliRunner, observer: RunObserver = new RecordingObserver()): ObservingCliRunner {
  return new ObservingCliRunner(runner, observer, [new CopilotLogReader(home)]);
}

describe('an agent built by a Workflow and run through a fake CLI runner', () => {
  test('returns the raw stdout of the CLI and reports the usage read from the session log', async () => {
    installLog(NAME);
    const runner = new RecordingRunner();
    const observer = new RecordingObserver();
    const client = new AgentBuilder(runner, null, new DockerRuntime(), new AgentContext({ cwd: home }))
      .withSession()
      .withObserver(observer, { readers: [new CopilotLogReader(home)] })
      .create(PROFILE, NAME);

    const result = await client.run(new AgentRequest('go'));

    expect(result.output).toBe('done:go');
    const [record] = observer.records;
    expect(record?.usage).toMatchObject({ apiCalls: 2, inputTokens: 500, outputTokens: 100, premiumRequests: 1 });
    expect(record?.output).toBe('done:go');
    expect(record?.tools.map((tool) => tool.name)).toEqual(['bash']);
    expect(record?.cwd).toBe(home);
    expect(runner.profiles[0]?.args.slice(-2)).toEqual(['--session-id', copilotSessionId(NAME)]);
  });

  test('a second observer is rejected', () => {
    const builder = new AgentBuilder(new FakeRunner(), null, new DockerRuntime()).withObserver(new RecordingObserver());

    expect(() => builder.withObserver(new RecordingObserver())).toThrow(ValueError);
  });

  test('no observer adds no argument', async () => {
    const runner = new RecordingRunner();

    await new AgentBuilder(runner, null, new DockerRuntime(), new AgentContext({ cwd: home })).create().run(new AgentRequest('go'));

    expect(runner.profiles[0]?.args).toEqual([]);
  });
});

describe('the observing runner', () => {
  test('pins a start, reports usage and returns the inner outcome', async () => {
    installLog(NAME);
    const inner = new FakeRunner();
    const observer = new RecordingObserver();

    const outcome = await observed(inner, observer).run(PROFILE, new AgentRequest('go'), new Start(NAME), new Context(new AgentContext({ cwd: home })));

    expect(outcome.output).toBe('ok');
    expect(inner.profiles[0]?.args.slice(-2)).toEqual(['--session-id', copilotSessionId(NAME)]);
    const [record] = observer.records;
    expect([record?.cli, record?.session, record?.exitCode, record?.error]).toEqual(['copilot', NAME.value, 0, null]);
    expect(record?.usage?.apiCalls).toBe(2);
    expect(record?.nativeId).toBe(copilotSessionId(NAME));
    expect(record?.prompt).toBe('go');
    expect(record?.output).toBe('ok');
    expect(record !== undefined && record.startedAt <= record.endedAt).toBe(true);
  });

  test('leaves the profile alone on a resume', async () => {
    const inner = new FakeRunner();

    await observed(inner).run(PROFILE, new AgentRequest('go'), new Resume(NAME, new NativeHandle('copilot', NAME.value)), new Context());

    expect(inner.profiles).toEqual([PROFILE]);
  });

  test('reports unobservable usage as null, never zero', async () => {
    const observer = new RecordingObserver();

    await observed(new FakeRunner(), observer).run(PROFILE, new AgentRequest('go'), new Start(NAME), new Context());

    const [record] = observer.records;
    expect([record?.usage, record?.tools, record?.nativeId, record?.model]).toEqual([null, [], null, 'claude-haiku-4.5']);
  });

  test('a CLI without a reader gets no argument', async () => {
    const inner = new FakeRunner();
    const observer = new RecordingObserver();

    await new ObservingCliRunner(inner, observer).run(PROFILE, new AgentRequest('go'), new Start(NAME), new Context());

    expect(inner.profiles).toEqual([PROFILE]);
    expect(observer.records[0]?.usage).toBeNull();
  });

  test('reports a failure and then rethrows it', async () => {
    const failure = new ProcessFailedError({ argv: ['copilot'], exitCode: 2, signal: null, stderr: '', stdout: '' });
    const observer = new RecordingObserver();

    const rejected = await rejectionOf(observed(new FakeRunner(failure), observer).run(PROFILE, new AgentRequest('go'), new Start(NAME), new Context()));

    expect(rejected).toBe(failure);
    const [record] = observer.records;
    expect(record?.exitCode).toBeNull();
    expect(record?.error).toContain('ProcessFailedError');
    expect(record?.output).toBeNull();
  });

  test('a failing observer never fails the run', async () => {
    const outcome = await observed(new FakeRunner(), brokenObserver).run(PROFILE, new AgentRequest('go'), new Start(NAME), new Context());

    expect(outcome.exitCode).toBe(0);
  });

  test('a garbled log never fails the run', async () => {
    const path = installLog(NAME);
    writeFileSync(path, '{not json\n');
    const observer = new RecordingObserver();

    const outcome = await observed(new FakeRunner(), observer).run(PROFILE, new AgentRequest('go'), new Start(NAME), new Context());

    expect(outcome.exitCode).toBe(0);
    expect(observer.records[0]?.usage).toBeNull();
  });
});
