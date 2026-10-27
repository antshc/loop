import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { AgentProfile } from '../clis/base.ts';
import type { SessionLogReader } from '../clis/observing.ts';
import type { CliRunner } from '../clis/runner.ts';
import type { GitOptions, GitService } from '../git/service.ts';
import type { RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';

import { LogReading, ToolCall, Usage } from '../observe/values.ts';
import { CliOutcome, NativeHandle, Resume, type Turn } from '../sessions/values.ts';

export interface FakeCliRunnerOptions {
  cli?: string;
  clock?: () => Date;
  /** Waits the given seconds; defaults to a real sleep. */
  sleep?: (seconds: number) => Promise<void>;
  /** Scales the tool time the fake waits; 0 never waits. */
  speed?: number;
}

export interface FakeReplyInit {
  model?: null | string;
  tools?: readonly FakeToolUse[];
  usage?: Usage;
}

export interface FakeToolUseInit {
  arguments?: Readonly<Record<string, unknown>>;
  ok?: boolean;
  result?: string;
  seconds?: number;
}

export type Respond = (request: AgentRequest, context: RunContext) => FakeReply | Promise<FakeReply>;

/** A `GitService` whose worktree is a fresh temporary directory: no repository, no git, no hooks. */
export class DirectoryGit implements GitService {
  readonly #root: null | string;

  constructor(root: null | string = null) {
    this.#root = root;
  }

  /** Runs `body` in an empty directory and removes it afterwards. */
  async open<T>(_cwd: string, _options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    const path = await mkdtemp(join(this.#root ?? tmpdir(), 'loop-fake-'));
    try {
      return await body(path);
    } finally {
      await rm(path, { force: true, recursive: true });
    }
  }
}

/**
 * Stands in for an agent CLI: a `CliRunner` that answers from `respond`, and the `SessionLogReader` that reports its usage and tools.
 * Pass the same instance as a reader to `withObserver` so an observer sees the fake's usage like a real CLI's.
 */
export class FakeCliRunner implements CliRunner, SessionLogReader {
  readonly cli: string;
  readonly #clock: () => Date;
  readonly #readings = new Map<string, LogReading>();
  readonly #respond: Respond;
  readonly #sleep: (seconds: number) => Promise<void>;
  readonly #speed: number;

  constructor(respond: Respond, options: FakeCliRunnerOptions = {}) {
    this.cli = options.cli ?? 'copilot';
    this.#respond = respond;
    this.#speed = options.speed ?? 1;
    this.#sleep = options.sleep ?? ((seconds) => Bun.sleep(seconds * 1000));
    this.#clock = options.clock ?? (() => new Date());
  }

  /** The reading of the turn just run, once; null for an unknown turn. */
  read(turn: Turn): Promise<LogReading | null> {
    const reading = this.#readings.get(turn.name.value) ?? null;
    this.#readings.delete(turn.name.value);
    return Promise.resolve(reading);
  }

  /** Runs one fake turn; whatever `respond` raises propagates as a failed run. */
  async run(profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext): Promise<CliOutcome> {
    const reply = await this.#respond(request, context);
    const handle = turn instanceof Resume ? turn.handle : new NativeHandle(this.cli, `fake-${crypto.randomUUID().replaceAll('-', '')}`);
    const started = this.#clock();
    const tools = this.#toolCalls(reply.tools, started);
    await this.#sleep(reply.tools.reduce((total, use) => total + use.seconds, 0) * this.#speed);
    this.#readings.set(
      turn.name.value,
      new LogReading({
        model: reply.model ?? profile.model,
        nativeId: handle.value,
        reasoningEffort: profile.reasoningEffort,
        tools,
        usage: reply.usage,
      }),
    );
    return new CliOutcome(reply.output, handle, 0);
  }

  /** The fake needs no extra CLI arguments. */
  startArgs(): readonly string[] {
    return [];
  }

  #toolCalls(uses: readonly FakeToolUse[], started: Date): ToolCall[] {
    let cursor = started;
    return uses.map((use, index) => {
      const ended = new Date(cursor.getTime() + use.seconds * this.#speed * 1000);
      const call = new ToolCall({
        arguments: use.arguments,
        callId: `fake-call-${String(index + 1)}`,
        endedAt: ended,
        name: use.name,
        ok: use.ok,
        result: use.result,
        startedAt: cursor,
      });
      cursor = ended;
      return call;
    });
  }
}

/** What the fake agent answers: its output and the usage and tools its session log would show. */
export class FakeReply {
  readonly model: null | string;
  readonly output: string;
  readonly tools: readonly FakeToolUse[];
  readonly usage: Usage;

  constructor(output: string, init: FakeReplyInit = {}) {
    this.output = output;
    this.usage = init.usage ?? new Usage();
    this.tools = Object.freeze([...(init.tools ?? [])]);
    this.model = init.model ?? null;
    Object.freeze(this);
  }
}

/** One tool call the fake agent pretends to make. */
export class FakeToolUse {
  readonly arguments: Readonly<Record<string, unknown>>;
  readonly name: string;
  readonly ok: boolean;
  readonly result: string;
  readonly seconds: number;

  constructor(name: string, init: FakeToolUseInit = {}) {
    this.name = name;
    this.arguments = Object.freeze({ ...init.arguments });
    this.result = init.result ?? '';
    this.ok = init.ok ?? true;
    this.seconds = init.seconds ?? 0.5;
    Object.freeze(this);
  }
}
