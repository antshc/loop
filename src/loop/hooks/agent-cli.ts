import { fileURLToPath } from 'node:url';

import type { Turn } from '../sessions/values.ts';

import { join } from '../shell.ts';
import { ValueError } from '../value-error.ts';

export interface AgentCliHookWiringInit {
  args?: readonly string[];
  env?: Readonly<Record<string, string>>;
  /** Paths are relative to the agent cwd. */
  files?: ReadonlyMap<string, string>;
  gitExcludes?: readonly string[];
}

/** Moment in a CLI run at which a native hook fires. */
export const AgentCliHookPoint = {
  AGENT_STOP: 'agent-stop',
  POST_TOOL: 'post-tool',
  PRE_COMPACT: 'pre-compact',
  PRE_TOOL: 'pre-tool',
  PROMPT_SUBMITTED: 'prompt-submitted',
  SESSION_END: 'session-end',
  SESSION_START: 'session-start',
  SUBAGENT_START: 'subagent-start',
  SUBAGENT_STOP: 'subagent-stop',
} as const;
export type AgentCliHookPoint = (typeof AgentCliHookPoint)[keyof typeof AgentCliHookPoint];

/** Every hook point in firing order: `ordered` follows it, then declaration order. */
export const AGENT_CLI_HOOK_POINTS: readonly AgentCliHookPoint[] = [
  'session-start',
  'session-end',
  'agent-stop',
  'prompt-submitted',
  'pre-tool',
  'post-tool',
  'subagent-start',
  'subagent-stop',
  'pre-compact',
];

const DEFAULT_TIMEOUT_SEC = 30;
const SHIM = fileURLToPath(new URL('shim.ts', import.meta.url));

/** Base of shell commands run by the CLI's native hook at one fixed `point`; observe-only. Use a subclass. */
export abstract class AgentCliHook {
  readonly command: string;
  abstract readonly point: AgentCliHookPoint;
  readonly timeoutSec: number;

  protected constructor(command: string, timeoutSec: number = DEFAULT_TIMEOUT_SEC) {
    if (new.target === AgentCliHook) {
      throw new TypeError('AgentCliHook fixes no hook point; use a point subclass');
    }
    if (command === '') {
      throw new ValueError('hook command must not be empty');
    }
    if (timeoutSec <= 0) {
      throw new ValueError('hook timeoutSec must be positive');
    }
    this.command = command;
    this.timeoutSec = timeoutSec;
  }
}

/** What the runner applies around one CLI process; `files` paths are relative to the agent cwd. */
export class AgentCliHookWiring {
  readonly args: readonly string[];
  readonly env: Readonly<Record<string, string>>;
  readonly files: ReadonlyMap<string, string>;
  readonly gitExcludes: readonly string[];

  constructor(init: AgentCliHookWiringInit = {}) {
    this.args = Object.freeze([...(init.args ?? [])]);
    this.files = new Map(init.files);
    this.gitExcludes = Object.freeze([...(init.gitExcludes ?? [])]);
    this.env = Object.freeze({ ...init.env });
    Object.freeze(this);
  }
}

export class AgentStopAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.AGENT_STOP;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class PostToolAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.POST_TOOL;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class PreCompactAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.PRE_COMPACT;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class PreToolAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.PRE_TOOL;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class PromptSubmittedAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.PROMPT_SUBMITTED;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class SessionEndAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.SESSION_END;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class SessionStartAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.SESSION_START;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class SubagentStartAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.SUBAGENT_START;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

export class SubagentStopAgentCliHook extends AgentCliHook {
  readonly point = AgentCliHookPoint.SUBAGENT_STOP;

  constructor(command: string, timeoutSec?: number) {
    super(command, timeoutSec);
    Object.freeze(this);
  }
}

/** A requested hook point cannot fire in the CLI. */
export class UnsupportedAgentCliHookPoint extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'UnsupportedAgentCliHookPoint';
  }
}

/** (point, hook) pairs in point order, then declaration order. */
export function ordered(hooks: readonly AgentCliHook[]): [AgentCliHookPoint, AgentCliHook][] {
  return AGENT_CLI_HOOK_POINTS.flatMap((point) =>
    hooks.filter((hook) => hook.point === point).map((hook): [AgentCliHookPoint, AgentCliHook] => [point, hook]),
  );
}

/** Shell command that runs the shim, which in turn runs the user's `hook.command`. */
export function shimCommand(cli: string, point: AgentCliHookPoint, turn: Turn, hook: AgentCliHook): string {
  return join([process.execPath, SHIM, '--cli', cli, '--point', point, '--session', turn.name.value, '--', hook.command]);
}
