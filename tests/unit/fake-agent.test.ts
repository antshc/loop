import { afterEach, beforeEach, describe, expect, spyOn, test } from 'bun:test';
import { existsSync } from 'node:fs';

import {
  Agent,
  AgentCliHookPoint,
  AgentProfile,
  AgentRequest,
  AgentRunRecord,
  codex,
  copilot,
  DirectoryGit,
  fakeAgent,
  FakeCliRunner,
  FakeReply,
  FakeToolUse,
  type RunObserver,
  SessionName,
  SessionStartAgentCliHook,
  Usage,
  type WorktreeEvent,
} from '../../src/loop/index.ts';

class Recorder implements RunObserver {
  readonly runs: AgentRunRecord[] = [];
  readonly worktrees: WorktreeEvent[] = [];

  agentRun(record: AgentRunRecord): void {
    this.runs.push(record);
  }

  worktree(event: WorktreeEvent): void {
    this.worktrees.push(event);
  }
}

function runner(options: { sleep?: (seconds: number) => Promise<void>; speed?: number } = {}): FakeCliRunner {
  const reply = new FakeReply('hello', {
    model: 'fake-1',
    tools: [new FakeToolUse('bash', { seconds: 2 })],
    usage: new Usage({ inputTokens: 10, outputTokens: 5 }),
  });
  return new FakeCliRunner(() => reply, { speed: 0, ...options });
}

describe('the fake agent CLI and worktree', () => {
  test('runs in a temporary directory that is removed afterwards', async () => {
    const paths: string[] = [];
    const git = new DirectoryGit();
    const spy: typeof git.open = (cwd, options, body) =>
      git.open(cwd, options, (path) => {
        paths.push(path);
        expect(existsSync(path)).toBe(true);
        return body(path);
      });

    const result = await fakeAgent(runner(), { open: spy }).withGit().create().run(new AgentRequest('hi'));

    expect(result.output).toBe('hello');
    expect(paths).toHaveLength(1);
    expect(existsSync(paths[0] ?? '')).toBe(false);
  });

  test('an observer sees the fake usage, model and tools like a real CLI', async () => {
    const fake = runner();
    const observer = new Recorder();

    await fakeAgent(fake).withObserver(observer, { readers: [fake] }).create().run(new AgentRequest('hi'));

    const [record] = observer.runs;
    expect(record?.usage).toEqual(new Usage({ inputTokens: 10, outputTokens: 5 }));
    expect(record?.model).toBe('fake-1');
    expect(record?.tools.map((tool) => tool.name)).toEqual(['bash']);
  });

  test('without the fake as reader the usage is unobservable', async () => {
    const observer = new Recorder();

    await fakeAgent(runner()).withObserver(observer, { readers: [] }).create().run(new AgentRequest('hi'));

    expect(observer.runs[0]?.usage).toBeNull();
  });

  test('speed scales the tool time the fake waits', async () => {
    const slept: number[] = [];
    const reply = new FakeReply('ok', { tools: [new FakeToolUse('bash', { seconds: 4 })] });
    const fake = new FakeCliRunner(() => reply, { sleep: (seconds) => Promise.resolve(void slept.push(seconds)), speed: 0.5 });

    await fakeAgent(fake).create().run(new AgentRequest('hi'));

    expect(slept).toEqual([2]);
  });

  test('a failing respond callback fails the run', async () => {
    const fake = new FakeCliRunner(() => {
      throw new Error('fake failed');
    });

    const rejected = await fakeAgent(fake)
      .create()
      .run(new AgentRequest('hi'))
      .catch((error: unknown) => error);

    expect(rejected).toMatchObject({ message: 'fake failed' });
  });
});

describe('dry run', () => {
  let log: ReturnType<typeof spyOn<Console, 'log'>>;

  beforeEach(() => {
    log = spyOn(console, 'log').mockImplementation(() => undefined);
  });

  afterEach(() => {
    log.mockRestore();
  });

  test('logs the command instead of running a CLI and returns it as the output', async () => {
    const result = await Agent({ dryRun: true }).create(new AgentProfile(copilot, { model: 'm' })).run(new AgentRequest('Review'));

    expect(result.output).toContain('cmd=copilot -p Review');
    expect(log).toHaveBeenCalledWith(`[dry-run] ${result.output}`);
  });

  test('logs the docker image the stub configures', async () => {
    await Agent({ dryRun: true }).withDocker().create().run(new AgentRequest('Review'));

    expect(log).toHaveBeenCalledWith('[dry-run] docker image=agent:latest');
  });

  test('Codex dry run invents a handle and a session resumes it', async () => {
    const client = Agent({ dryRun: true }).withSession().create(new AgentProfile(codex), new SessionName('s'));

    await client.run(new AgentRequest('one'));
    const second = await client.run(new AgentRequest('two'));

    expect(second.output).toContain('codex exec resume dry-');
  });

  test('logs each hook, its native name and the wiring', async () => {
    await Agent({ dryRun: true })
      .withAgentCliHooks(new SessionStartAgentCliHook('observe'))
      .create()
      .run(new AgentRequest('Review'));

    const lines = log.mock.calls.map((call) => String(call[0]));
    expect(lines).toContain(`[dry-run] hook copilot ${AgentCliHookPoint.SESSION_START} -> sessionStart: observe`);
    expect(lines.some((line) => line.startsWith('[dry-run] hook file '))).toBe(true);
    expect(lines.some((line) => line.startsWith('[dry-run] hook env '))).toBe(true);
  });
});
