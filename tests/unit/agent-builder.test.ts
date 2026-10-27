import { beforeEach, describe, expect, spyOn, test } from 'bun:test';

import type { ProcessRunner } from '../../src/loop/run/process.ts';

import {
  Agent,
  AgentBuilder,
  AgentCliHookPoint,
  AgentContext,
  AgentProfile,
  AgentRequest,
  AgentStartMode,
  CliAgentClient,
  CliOutcome,
  codex,
  CodexCli,
  copilot,
  DockerAgent,
  DockerRuntime,
  GitAgent,
  MemorySessionStore,
  NativeHandle,
  PreToolAgentCliHook,
  ProcessCliRunner,
  PromptSubmittedAgentCliHook,
  Resume,
  RunContext,
  SessionCliMismatch,
  SessionEndAgentCliHook,
  SessionName,
  SessionStartAgentCliHook,
  type SessionStore,
  Start,
  UnsupportedAgentCliHookPoint,
  ValueError,
} from '../../src/loop/index.ts';
import { ClosingClient, RecordingDocker, RecordingGit, RecordingRunner, rejectionOf, userCli } from './support/doubles.ts';

const REPO = '/repo';
const repoContext = (): AgentContext => new AgentContext({ cwd: REPO });

let events: string[] = [];
let runner = new RecordingRunner();

function builder(): AgentBuilder {
  return new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events));
}

beforeEach(() => {
  events = [];
  runner = new RecordingRunner(events);
});

describe('agent builder', () => {
  test('the default client has no wrappers', () => {
    expect(Agent().create()).toBeInstanceOf(CliAgentClient);
  });

  test('a run returns the raw output with the session name it ran under', async () => {
    const result = await builder().create().run(new AgentRequest('hello'), repoContext());

    expect(result.output).toBe('done:hello');
    expect(result.exitCode).toBe(0);
    expect(result.session.value.startsWith('loop-')).toBe(true);
  });

  test('runs are stateless without a session', async () => {
    const client = builder().create();
    await client.run(new AgentRequest('one'), repoContext());
    await client.run(new AgentRequest('two'), repoContext());

    const [first, second] = runner.turns;
    expect(first).toBeInstanceOf(Start);
    expect(second).toBeInstanceOf(Start);
    expect(first?.name).not.toEqual(second?.name);
  });

  test('a session is shared between runs and resumed by its native handle', async () => {
    const client = builder().withSession().create();
    const first = await client.run(new AgentRequest('one'), repoContext());
    const second = await client.run(new AgentRequest('two'), repoContext());

    expect(second.session).toEqual(first.session);
    expect(runner.turns[0]).toBeInstanceOf(Start);
    expect(runner.turns[1]).toEqual(new Resume(first.session, new NativeHandle('copilot', 'copilot1')));
  });

  test('a named session', async () => {
    const result = await builder().withSession().create(undefined, new SessionName('feat')).run(new AgentRequest('x'), repoContext());

    expect(result.session).toEqual(new SessionName('feat'));
  });

  test('a session without withSession() is rejected', () => {
    expect(() => builder().create(undefined, new SessionName('a'))).toThrow(ValueError);
  });

  test('a request name needs a stateless client', async () => {
    await builder().create().run(new AgentRequest('x', 'loop(1) T'), repoContext());
    expect(runner.turns[0]).toBeInstanceOf(Start);

    const rejected = await rejectionOf(builder().withSession().create().run(new AgentRequest('x', 'loop(1) T'), repoContext()));
    expect(rejected).toBeInstanceOf(ValueError);
  });

  test('the Copilot handle is saved before the run so a retry resumes', async () => {
    const store = new MemorySessionStore();
    runner.fail = true;
    const name = new SessionName('n');
    const client = new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events), null, store)
      .withSession()
      .create(undefined, name);

    expect(await rejectionOf(client.run(new AgentRequest('x'), repoContext()))).toMatchObject({ message: 'runner failed' });
    expect(await store.get(name, 'copilot')).toEqual(new NativeHandle('copilot', 'n'));

    runner.fail = false;
    await client.run(new AgentRequest('x'), repoContext());
    expect(runner.turns.at(-1)).toBeInstanceOf(Resume);
  });

  test('a stored handle of another CLI is a mismatch', async () => {
    const mismatched: SessionStore = {
      get: () => Promise.resolve(new NativeHandle('codex', 't')),
      save: () => Promise.resolve(),
    };
    const client = new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events), null, mismatched)
      .withSession()
      .create(undefined, new SessionName('n'));

    expect(await rejectionOf(client.run(new AgentRequest('x'), repoContext()))).toBeInstanceOf(SessionCliMismatch);
  });

  test('a session sees the git worktree', async () => {
    const client = builder().withGit().withSession().create();

    expect(client).toBeInstanceOf(GitAgent);
    await client.run(new AgentRequest('one'), repoContext());
    await client.run(new AgentRequest('two'), repoContext());
    expect(runner.turns[1]).toBeInstanceOf(Resume);
  });

  test('git is the outer wrapper and docker the inner one', async () => {
    const subject = builder();

    expect(subject.withGit()).toBe(subject);
    expect(subject.withDocker()).toBe(subject);
    const client = subject.create();
    expect(client).toBeInstanceOf(GitAgent);
    expect((await client.run(new AgentRequest('hello'), repoContext())).output).toBe('done:hello');
    expect(events).toEqual(['worktree.enter', 'docker.configure', 'cli.run', 'worktree.exit']);
    expect(runner.contexts[0]?.agent.cwd).toBe('/repo/worktree');
    expect(runner.contexts[0]?.agent.dockerImage).toBe('test-image');
  });

  test('the worktree is released when the run fails', async () => {
    runner.fail = true;

    expect(await rejectionOf(builder().withGit().create().run(new AgentRequest('broken')))).toMatchObject({ message: 'runner failed' });
    expect(events).toEqual(['worktree.enter', 'cli.run', 'worktree.exit']);
  });

  test('docker alone keeps the original workspace', async () => {
    const client = builder().withDocker().create();

    expect(client).toBeInstanceOf(DockerAgent);
    await client.run(new AgentRequest('hello'), repoContext());
    expect(events).toEqual(['docker.configure', 'cli.run']);
    expect(runner.contexts[0]?.agent.cwd).toBe(REPO);
  });

  test('git needs a git service', () => {
    expect(() => new AgentBuilder(runner, null, new DockerRuntime()).withGit()).toThrow(ValueError);
  });

  test('close is delegated through the wrappers', async () => {
    const inner = new ClosingClient();

    await new GitAgent(new DockerAgent(inner, new RecordingDocker(events)), new RecordingGit(events)).close();

    expect(inner.closeCount).toBe(1);
  });
});

describe('agent CLI hooks', () => {
  const hooked = (): AgentBuilder =>
    new AgentBuilder(runner, new RecordingGit(events), new RecordingDocker(events), new AgentContext({ cwd: REPO }));

  test('at least one hook is required', () => {
    expect(() => hooked().withAgentCliHooks()).toThrow(ValueError);
  });

  test('a string becomes session start and end hooks', async () => {
    await hooked().withAgentCliHooks('cmd').create().run(new AgentRequest('x'));

    expect(runner.contexts[0]?.agentCliHooks).toEqual([new SessionStartAgentCliHook('cmd'), new SessionEndAgentCliHook('cmd')]);
  });

  test('hooks reach the runner in order, also through git', async () => {
    const first = new SessionStartAgentCliHook('a');
    const second = new SessionEndAgentCliHook('b');
    await hooked().withGit().withAgentCliHooks(first, second).create().run(new AgentRequest('x'));

    expect(runner.contexts[0]?.agentCliHooks).toEqual([first, second]);
  });

  test('Copilot accepts a prompt-submitted hook', async () => {
    await hooked().withAgentCliHooks(new PromptSubmittedAgentCliHook('c')).create(new AgentProfile(copilot)).run(new AgentRequest('x'));

    expect(runner.requests).toHaveLength(1);
  });

  test('an unsupported hook point fails before git is touched', () => {
    const noPoints = userCli('nopoints', new Set([AgentCliHookPoint.SESSION_START]));
    const subject = hooked().withGit().withAgentCliHooks(new PreToolAgentCliHook('c'));

    expect(() => subject.create(new AgentProfile(noPoints))).toThrow(UnsupportedAgentCliHookPoint);
    expect(events).toEqual([]);
  });

  test('Codex needs the hook trust bypass', () => {
    const subject = hooked().withAgentCliHooks('c');

    expect(() => subject.create(new AgentProfile(codex))).toThrow(UnsupportedAgentCliHookPoint);
    expect(() => subject.create(new AgentProfile(new CodexCli(undefined, { hookTrustBypass: true })))).not.toThrow();
  });
});

describe('profile context', () => {
  const profile = new AgentProfile(copilot, {
    addDirs: ['/tmp/docs'],
    cwd: '/tmp/target',
    env: { APP: 'cart' },
    timeoutSec: 12,
  });

  test('is resolved before git and docker see it', async () => {
    await builder().withGit().withDocker().create(profile).run(new AgentRequest('Implement'));

    const context = runner.contexts[0]?.agent;
    expect(context?.cwd).toBe('/tmp/target/worktree');
    expect(context?.addDirs).toEqual(['/tmp/docs']);
    expect(context?.env).toEqual({ APP: 'cart' });
    expect(context?.timeoutSec).toBe(12);
    expect(context?.dockerImage).toBe('test-image');
  });

  test('an explicit run context replaces the profile defaults completely', async () => {
    const override = new AgentContext({ cwd: '/tmp/override', env: { APP: 'checkout' }, timeoutSec: 4 });
    await builder().create(profile).run(new AgentRequest('Review'), override);

    expect(runner.contexts[0]?.agent).toEqual(override);
  });

  test('a harness launch applies after the profile context is resolved', async () => {
    const context = new AgentContext({ harnessDir: '/tmp/harness', startMode: AgentStartMode.HARNESS });
    await new AgentBuilder(runner, null, new DockerRuntime(), context)
      .create(new AgentProfile(copilot, { cwd: '/tmp/target' }))
      .run(new AgentRequest('Review'));

    expect(runner.contexts[0]?.agent.cwd).toBe('/tmp/harness');
    expect(runner.contexts[0]?.agent.addDirs).toContain('/tmp/target');
    expect(runner.requests[0]?.prompt).toContain('/tmp/target');
  });

  test('the harness start mode prefixes resumed sessions too', async () => {
    const context = new AgentContext({ cwd: REPO, harnessDir: '/harness', startMode: AgentStartMode.HARNESS });
    const client = new AgentBuilder(runner, null, new DockerRuntime(), context).withSession().create();

    await client.run(new AgentRequest('first'));
    await client.run(new AgentRequest('second'));

    expect(runner.turns[1]).toBeInstanceOf(Resume);
    expect(runner.requests.every((request) => request.prompt.includes('change into it'))).toBe(true);
    expect(runner.requests[1]?.prompt.endsWith('\n\nsecond')).toBe(true);
  });

  const quiet = (): { mockRestore: () => void } => spyOn(console, 'log').mockImplementation(() => undefined);

  test('Agent options configure the builder context', async () => {
    const silence = quiet();
    const dry = Agent({ dryRun: true, harnessDir: '/harness', startMode: AgentStartMode.HARNESS });
    const result = await dry.create().run(new AgentRequest('do it'));

    silence.mockRestore();
    expect(result.output).toContain('cwd=/harness');
  });

  test('Agent options without a start mode keep the worktree mode', async () => {
    const silence = quiet();
    const result = await Agent({ dryRun: true }).create().run(new AgentRequest('do it'));

    silence.mockRestore();
    expect(result.output).toContain(`cwd=${process.cwd()} `);
  });
});

describe('a user-defined CLI', () => {
  test('runs through the process runner', async () => {
    const calls: { argv: readonly string[]; options: unknown }[] = [];
    const processes: ProcessRunner = {
      run: (argv, options) => {
        calls.push({ argv, options });
        return Promise.resolve({ argv, exitCode: 0, signal: null, stderr: '', stdout: 'out' });
      },
      start: () => {
        throw new Error('not used');
      },
    };

    const outcome = await new ProcessCliRunner(processes).run(
      new AgentProfile(userCli(), { args: ['-x'] }),
      new AgentRequest('hi'),
      new Start(new SessionName('s')),
      new RunContext(repoContext()),
    );

    expect(outcome).toEqual(new CliOutcome('OUT', new NativeHandle('user', 'fixed'), 0));
    expect(calls[0]?.argv).toEqual(['user-cli', 'hi', 's', '-x']);
    expect(calls[0]?.options).toMatchObject({ check: true, cwd: REPO });
  });
});
