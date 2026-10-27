import {
  type AgentCli,
  type AgentClient,
  type AgentCliHookPoint,
  AgentCliHookWiring,
  type AgentContext,
  type AgentProfile,
  type AgentRequest,
  type AgentResult,
  CliOutcome,
  type CliRunner,
  CopilotText,
  type DockerService,
  type GitOptions,
  type GitService,
  NativeHandle,
  AgentResult as Result,
  Resume,
  type RunContext,
  SessionName,
  type Turn,
} from '../../../src/loop/index.ts';

export class ClosingClient implements AgentClient {
  closeCount = 0;

  close(): Promise<void> {
    this.closeCount++;
    return Promise.resolve();
  }

  run(request: AgentRequest): Promise<AgentResult> {
    return Promise.resolve(new Result(request.prompt, new SessionName('s'), 0));
  }
}

export class RecordingDocker implements DockerService {
  constructor(private readonly events: string[]) {}

  configure(context: AgentContext): Promise<AgentContext> {
    this.events.push('docker.configure');
    return Promise.resolve(context.with({ dockerImage: 'test-image' }));
  }
}

export class RecordingGit implements GitService {
  constructor(private readonly events: string[]) {}

  async open<T>(cwd: string, _options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    this.events.push('worktree.enter');
    try {
      return await body(`${cwd}/worktree`);
    } finally {
      this.events.push('worktree.exit');
    }
  }
}

export class RecordingRunner implements CliRunner {
  readonly contexts: RunContext[] = [];
  fail = false;
  readonly profiles: AgentProfile[] = [];
  readonly requests: AgentRequest[] = [];
  readonly turns: Turn[] = [];

  constructor(private readonly events: string[] = []) {}

  run(profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext): Promise<CliOutcome> {
    this.events.push('cli.run');
    this.profiles.push(profile);
    this.contexts.push(context);
    this.requests.push(request);
    this.turns.push(turn);
    if (this.fail) {
      return Promise.reject(new Error('runner failed'));
    }
    const handle =
      turn instanceof Resume ? turn.handle : new NativeHandle(profile.cli.name, `${profile.cli.name}${String(this.requests.length)}`);
    return Promise.resolve(new CliOutcome(`done:${request.prompt}`, handle, 0));
  }
}

/** The error a promise rejects with, or undefined when it resolves. */
export async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  return undefined;
}

/** A user-defined CLI that upper-cases stdout and claims the given hook points. */
export function userCli(name = 'user', hookPoints: ReadonlySet<AgentCliHookPoint> = new Set()): AgentCli {
  return {
    command: (request, profile, turn) => ['user-cli', request.prompt, turn.name.value, ...profile.args],
    handleForNew: () => new NativeHandle(name, 'fixed'),
    hookPoints,
    hookWiring: () => new AgentCliHookWiring(),
    name,
    nativePoint: (point) => point,
    parse: (stdout, _turn, exitCode) => new CliOutcome(stdout.toUpperCase(), new NativeHandle(name, 'fixed'), exitCode),
    transport: new CopilotText(),
  };
}
