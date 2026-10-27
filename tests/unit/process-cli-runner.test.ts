import { describe, expect, test } from 'bun:test';

import {
  type AgentCli,
  AgentCliHookWiring,
  AgentContext,
  AgentProfile,
  AgentRequest,
  CliOutcome,
  copilot,
  CopilotText,
  NativeHandle,
  ProcessCliRunner,
  ProcessFailedError,
  RunContext,
  SessionName,
  SessionStartAgentCliHook,
  Start,
} from '../../src/loop/index.ts';
import { type ProcessOptions, type ProcessRunner } from '../../src/loop/run/process.ts';
import { rejectionOf, userCli } from './support/doubles.ts';

const TURN = new Start(new SessionName('s'));

class FakeProcesses implements ProcessRunner {
  readonly calls: { argv: readonly string[]; options: ProcessOptions | undefined }[] = [];

  constructor(
    private readonly result: { exitCode?: number; stdout?: string } = {},
    private readonly failure: Error | null = null,
  ) {}

  run(argv: readonly string[], options?: ProcessOptions): ReturnType<ProcessRunner['run']> {
    this.calls.push({ argv, options });
    if (this.failure !== null) {
      return Promise.reject(this.failure);
    }
    return Promise.resolve({ argv, exitCode: this.result.exitCode ?? 0, signal: null, stderr: '', stdout: this.result.stdout ?? 'ok' });
  }

  start(): never {
    throw new Error('not used');
  }
}

const runWith = (processes: ProcessRunner, profile: AgentProfile, context: RunContext): Promise<CliOutcome> =>
  new ProcessCliRunner(processes).run(profile, new AgentRequest('Review'), TURN, context);

describe('the process CLI runner', () => {
  test('runs the command in the agent cwd, checking the exit status', async () => {
    const processes = new FakeProcesses();

    await runWith(processes, new AgentProfile(copilot), new RunContext(new AgentContext({ cwd: '/work' })));

    expect(processes.calls[0]?.argv[0]).toBe('copilot');
    expect(processes.calls[0]?.options).toMatchObject({ check: true, cwd: '/work' });
    expect(processes.calls[0]?.options?.env).toBeUndefined();
    expect(processes.calls[0]?.options?.timeoutSec).toBeUndefined();
  });

  test('forwards the environment overrides and the timeout', async () => {
    const processes = new FakeProcesses();
    const context = new AgentContext({ cwd: '/work', env: { APP: 'cart' }, timeoutSec: 30 });

    await runWith(processes, new AgentProfile(copilot), new RunContext(context));

    expect(processes.calls[0]?.options).toMatchObject({ env: { APP: 'cart' }, timeoutSec: 30 });
  });

  test('hook wiring environment takes precedence over profile values', async () => {
    const wired: AgentCli = {
      ...userCli(),
      hookPoints: new Set(['session-start']),
      hookWiring: () => new AgentCliHookWiring({ env: { HOOK_STATE: 'wired' } }),
    };
    const processes = new FakeProcesses();
    const context = new RunContext(new AgentContext({ cwd: '/work', env: { APP: 'cart', HOOK_STATE: 'caller' } }), [
      new SessionStartAgentCliHook('observe'),
    ]);

    await runWith(processes, new AgentProfile(wired), context);

    expect(processes.calls[0]?.options?.env).toEqual({ APP: 'cart', HOOK_STATE: 'wired' });
  });

  test('parses the raw stdout and exit code through the CLI', async () => {
    const outcome = await runWith(new FakeProcesses({ stdout: 'raw text' }), new AgentProfile(userCli()), new RunContext());

    expect(outcome).toEqual(new CliOutcome('RAW TEXT', new NativeHandle('user', 'fixed'), 0));
  });

  test('a failing process raises its error rather than becoming a result', async () => {
    const failure = new ProcessFailedError({ argv: ['copilot'], exitCode: 1, signal: null, stderr: 'boom', stdout: '' });

    const rejected = await rejectionOf(runWith(new FakeProcesses({}, failure), new AgentProfile(copilot), new RunContext()));

    expect(rejected).toBe(failure);
  });
});

describe('captured process output', () => {
  const printing = (script: string): AgentCli => ({
    ...userCli('printing'),
    command: () => ['sh', '-c', script],
    parse: (stdout, _turn, exitCode) => new CliOutcome(stdout, new NativeHandle('printing', 'h'), exitCode),
    transport: new CopilotText(),
  });

  test('invalid UTF-8 becomes U+FFFD and is never an error', async () => {
    const outcome = await new ProcessCliRunner().run(
      new AgentProfile(printing(String.raw`printf '\377ok\376'`)),
      new AgentRequest('x'),
      TURN,
      new RunContext(new AgentContext({ cwd: process.cwd() })),
    );

    expect(outcome.output).toBe('\uFFFDok\uFFFD');
  });

  test('a non-zero exit of the real process raises ProcessFailedError', async () => {
    const rejected = await rejectionOf(
      new ProcessCliRunner().run(
        new AgentProfile(printing('echo partial; exit 3')),
        new AgentRequest('x'),
        TURN,
        new RunContext(new AgentContext({ cwd: process.cwd() })),
      ),
    );

    expect(rejected).toBeInstanceOf(ProcessFailedError);
    expect(rejected).toMatchObject({ exitCode: 3, stdout: 'partial\n' });
  });
});
