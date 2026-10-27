/** Facts reported about Agent Runs and Worktrees; observers receive them through `RunObserver`. */

export interface AgentRunRecordInit {
  agent?: null | string;
  cli: string;
  cwd: string;
  endedAt: Date;
  error: null | string;
  /** Null when the runner raised; `error` then holds the failure. */
  exitCode: null | number;
  model: null | string;
  nativeId: null | string;
  output: null | string;
  prompt: string;
  reasoningEffort: null | string;
  session: string;
  startedAt: Date;
  tools: readonly ToolCall[];
  usage: null | Usage;
}

export interface LogReadingInit {
  model: null | string;
  nativeId: string;
  reasoningEffort: null | string;
  tools: readonly ToolCall[];
  usage: Usage;
}

/** Receives facts about Agent Runs and Worktrees; may be async, and a failure never fails the run. */
export interface RunObserver {
  agentRun: (record: AgentRunRecord) => Promise<void> | void;
  worktree: (event: WorktreeEvent) => Promise<void> | void;
}

export interface ToolCallInit {
  arguments: Readonly<Record<string, unknown>>;
  callId: string;
  endedAt: Date;
  name: string;
  ok: boolean;
  result: string;
  startedAt: Date;
}

export interface UsageInit {
  /** GitHub AI units per token type. */
  aiu?: Readonly<Record<string, number>>;
  apiCalls?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  /** Uncached input only; cache reads and writes are counted apart. */
  inputTokens?: number;
  outputTokens?: number;
  premiumRequests?: number;
  reasoningTokens?: number;
}

export interface WorktreeEventInit {
  at: Date;
  ok: boolean | null;
  path: string;
  point: WorktreePoint;
}

export type WorktreePoint = 'ready' | 'removed' | 'removing' | 'retained';

/** One finished Agent Run turn; `usage` null or `tools` empty means unobservable, never zero. */
export class AgentRunRecord {
  /** The profile's name; null for an unnamed agent. */
  readonly agent: null | string;
  readonly cli: string;
  readonly cwd: string;
  readonly endedAt: Date;
  readonly error: null | string;
  readonly exitCode: null | number;
  readonly model: null | string;
  readonly nativeId: null | string;
  readonly output: null | string;
  readonly prompt: string;
  readonly reasoningEffort: null | string;
  readonly session: string;
  readonly startedAt: Date;
  readonly tools: readonly ToolCall[];
  readonly usage: null | Usage;

  constructor(init: AgentRunRecordInit) {
    this.cli = init.cli;
    this.session = init.session;
    this.nativeId = init.nativeId;
    this.cwd = init.cwd;
    this.startedAt = init.startedAt;
    this.endedAt = init.endedAt;
    this.exitCode = init.exitCode;
    this.error = init.error;
    this.model = init.model;
    this.reasoningEffort = init.reasoningEffort;
    this.usage = init.usage;
    this.tools = Object.freeze([...init.tools]);
    this.prompt = init.prompt;
    this.output = init.output;
    this.agent = init.agent ?? null;
    Object.freeze(this);
  }
}

/** What a CLI's own session log says about one turn. */
export class LogReading {
  readonly model: null | string;
  readonly nativeId: string;
  readonly reasoningEffort: null | string;
  readonly tools: readonly ToolCall[];
  readonly usage: Usage;

  constructor(init: LogReadingInit) {
    this.nativeId = init.nativeId;
    this.model = init.model;
    this.reasoningEffort = init.reasoningEffort;
    this.usage = init.usage;
    this.tools = Object.freeze([...init.tools]);
    Object.freeze(this);
  }
}

/** One tool call with the real start and end the CLI logged. */
export class ToolCall {
  readonly arguments: Readonly<Record<string, unknown>>;
  readonly callId: string;
  readonly endedAt: Date;
  readonly name: string;
  readonly ok: boolean;
  readonly result: string;
  readonly startedAt: Date;

  constructor(init: ToolCallInit) {
    this.callId = init.callId;
    this.name = init.name;
    this.arguments = Object.freeze({ ...init.arguments });
    this.result = init.result;
    this.ok = init.ok;
    this.startedAt = init.startedAt;
    this.endedAt = init.endedAt;
    Object.freeze(this);
  }
}

/** Model usage of one Agent Run turn. */
export class Usage {
  readonly aiu: Readonly<Record<string, number>>;
  readonly apiCalls: number;
  readonly cacheReadTokens: number;
  readonly cacheWriteTokens: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly premiumRequests: number;
  readonly reasoningTokens: number;

  /** AI units over every token type. */
  get totalAiu(): number {
    return Object.values(this.aiu).reduce((sum, value) => sum + value, 0);
  }

  /** Input tokens as the OpenTelemetry GenAI conventions count them: uncached plus cache reads and writes. */
  get totalInputTokens(): number {
    return this.inputTokens + this.cacheReadTokens + this.cacheWriteTokens;
  }

  constructor(init: UsageInit = {}) {
    this.apiCalls = init.apiCalls ?? 0;
    this.inputTokens = init.inputTokens ?? 0;
    this.outputTokens = init.outputTokens ?? 0;
    this.reasoningTokens = init.reasoningTokens ?? 0;
    this.cacheReadTokens = init.cacheReadTokens ?? 0;
    this.cacheWriteTokens = init.cacheWriteTokens ?? 0;
    this.aiu = Object.freeze({ ...init.aiu });
    this.premiumRequests = init.premiumRequests ?? 0;
    Object.freeze(this);
  }
}

/** A Worktree lifecycle fact; `retained` ends a run without deleting its directory. */
export class WorktreeEvent {
  readonly at: Date;
  readonly ok: boolean | null;
  readonly path: string;
  readonly point: WorktreePoint;

  constructor(init: WorktreeEventInit) {
    this.point = init.point;
    this.path = init.path;
    this.at = init.at;
    this.ok = init.ok;
    Object.freeze(this);
  }
}
