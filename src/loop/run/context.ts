import type { AgentCliHook } from '../hooks/agent-cli.ts';

import { ValueError } from '../value-error.ts';
import { freezeEnv, validateTimeout } from './settings.ts';

export interface AgentContextInit {
  /** Extra directories the agent may access (`--add-dir`); the cwd is always included by the CLI. */
  addDirs?: readonly string[];
  cwd?: string;
  dockerImage?: null | string;
  env?: null | Readonly<Record<string, string>>;
  /** The harness checkout the CLI starts in; required by the harness start mode. */
  harnessDir?: null | string;
  startMode?: AgentStartMode;
  timeoutSec?: null | number;
}

/** Optional caller overrides; unset fields keep the AgentContext defaults. */
export interface AgentOptions {
  readonly dockerImage?: string;
  /** Runs stub adapters that only log to the console: no git, Docker or CLI process. */
  readonly dryRun?: boolean;
  readonly harnessDir?: string;
  readonly startMode?: AgentStartMode;
}

/** Where an agent CLI process starts relative to the worktree it works on. */
export const AgentStartMode = {
  // Launch in the harness repository; the prompt is prefixed with an instruction to change into the worktree.
  HARNESS: 'harness',
  // Launch directly in the worktree.
  WORKTREE: 'worktree',
} as const;
export type AgentStartMode = (typeof AgentStartMode)[keyof typeof AgentStartMode];

/** Agent-level settings shared by every CLI. */
export class AgentContext {
  readonly addDirs: readonly string[];
  readonly cwd: string;
  readonly dockerImage: null | string;
  readonly env: null | Readonly<Record<string, string>>;
  readonly harnessDir: null | string;
  readonly startMode: AgentStartMode;
  readonly timeoutSec: null | number;

  constructor(init: AgentContextInit = {}) {
    validateTimeout(init.timeoutSec);
    this.cwd = init.cwd ?? process.cwd();
    this.dockerImage = init.dockerImage ?? null;
    this.addDirs = Object.freeze([...(init.addDirs ?? [])]);
    this.startMode = parseStartMode(init.startMode ?? AgentStartMode.WORKTREE);
    this.harnessDir = init.harnessDir ?? null;
    this.env = freezeEnv(init.env);
    this.timeoutSec = init.timeoutSec ?? null;
    if (this.startMode === AgentStartMode.HARNESS && this.harnessDir === null) {
      throw new ValueError("startMode 'harness' requires harnessDir");
    }
    Object.freeze(this);
  }

  /** A copy with `changes` applied; a null clears a nullable setting. */
  with(changes: AgentContextInit): AgentContext {
    return new AgentContext({
      addDirs: this.addDirs,
      cwd: this.cwd,
      dockerImage: this.dockerImage,
      env: this.env,
      harnessDir: this.harnessDir,
      startMode: this.startMode,
      timeoutSec: this.timeoutSec,
      ...changes,
    });
  }
}

export class RunContext {
  readonly agent: AgentContext;
  readonly agentCliHooks: readonly AgentCliHook[];

  constructor(agent: AgentContext = new AgentContext(), agentCliHooks: readonly AgentCliHook[] = []) {
    this.agent = agent;
    this.agentCliHooks = Object.freeze([...agentCliHooks]);
    Object.freeze(this);
  }

  with(changes: { agent?: AgentContext; agentCliHooks?: readonly AgentCliHook[] }): RunContext {
    return new RunContext(changes.agent ?? this.agent, changes.agentCliHooks ?? this.agentCliHooks);
  }
}

// Accepts the mode's string value; an unknown value is rejected.
function parseStartMode(mode: string): AgentStartMode {
  const known = Object.values(AgentStartMode).find((value) => value === mode);
  if (known === undefined) {
    throw new ValueError(`${JSON.stringify(mode)} is not a valid AgentStartMode`);
  }
  return known;
}
