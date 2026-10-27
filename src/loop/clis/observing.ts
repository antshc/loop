import type { LogReading, RunObserver } from '../observe/values.ts';
import type { RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';
import type { CliOutcome, Turn } from '../sessions/values.ts';
import type { AgentProfile } from './base.ts';
import type { CliRunner } from './runner.ts';

import { AgentRunRecord } from '../observe/values.ts';
import { describeError } from '../text.ts';

export interface ObservingCliRunnerOptions {
  clock?: () => Date;
}

/** Adapter for one CLI's own session log; keyed by `cli`, the `AgentCli.name` it serves. */
export interface SessionLogReader {
  readonly cli: string;
  /** This turn's usage and tools; null when unobservable. A malformed log may raise. */
  read: (turn: Turn) => Promise<LogReading | null>;
  /** Extra CLI arguments that make the log findable from `turn`; empty on a Resume. */
  startArgs: (turn: Turn) => readonly string[];
}

/** Decorates a CliRunner: after each turn it reports an AgentRunRecord to the observer and never changes the result. */
export class ObservingCliRunner implements CliRunner {
  readonly #clock: () => Date;
  readonly #inner: CliRunner;
  readonly #observer: RunObserver;
  readonly #readers: ReadonlyMap<string, SessionLogReader>;

  constructor(
    inner: CliRunner,
    observer: RunObserver,
    readers: readonly SessionLogReader[] = [],
    options: ObservingCliRunnerOptions = {},
  ) {
    this.#inner = inner;
    this.#observer = observer;
    this.#readers = new Map(readers.map((reader) => [reader.cli, reader]));
    this.#clock = options.clock ?? (() => new Date());
  }

  async run(profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext): Promise<CliOutcome> {
    const reader = this.#readers.get(profile.cli.name) ?? null;
    const startedAt = this.#clock();
    let outcome: CliOutcome;
    try {
      outcome = await this.#inner.run(pinned(profile, reader, turn), request, turn, context);
    } catch (error) {
      await this.#report(profile, request, turn, context, startedAt, reader, null, describeError(error));
      throw error;
    }
    await this.#report(profile, request, turn, context, startedAt, reader, outcome, null);
    return outcome;
  }

  async #read(reader: null | SessionLogReader, turn: Turn): Promise<LogReading | null> {
    if (reader === null) {
      return null;
    }
    try {
      return await reader.read(turn);
    } catch {
      // A log that cannot be read leaves the usage unobservable; it never fails the run.
      return null;
    }
  }

  async #report(
    profile: AgentProfile,
    request: AgentRequest,
    turn: Turn,
    context: RunContext,
    startedAt: Date,
    reader: null | SessionLogReader,
    outcome: CliOutcome | null,
    error: null | string,
  ): Promise<void> {
    const reading = await this.#read(reader, turn);
    const record = new AgentRunRecord({
      agent: profile.name,
      cli: profile.cli.name,
      cwd: context.agent.cwd,
      endedAt: this.#clock(),
      error,
      exitCode: outcome?.exitCode ?? null,
      model: reading?.model ?? profile.model,
      nativeId: reading?.nativeId ?? null,
      output: outcome?.output ?? null,
      prompt: request.prompt,
      reasoningEffort: reading?.reasoningEffort ?? profile.reasoningEffort,
      session: turn.name.value,
      startedAt,
      tools: reading?.tools ?? [],
      usage: reading?.usage ?? null,
    });
    try {
      await this.#observer.agentRun(record);
    } catch {
      // An observer is caller code; it must never fail the run.
    }
  }
}

function pinned(profile: AgentProfile, reader: null | SessionLogReader, turn: Turn): AgentProfile {
  const extra = reader?.startArgs(turn) ?? [];
  return extra.length > 0 ? profile.with({ args: [...profile.args, ...extra] }) : profile;
}
