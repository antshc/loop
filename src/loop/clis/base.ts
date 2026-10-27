import { isAbsolute } from 'node:path';

import type { AgentCliHook, AgentCliHookPoint, AgentCliHookWiring } from '../hooks/agent-cli.ts';
import type { AgentContext, RunContext } from '../run/context.ts';
import type { Instructions } from '../run/instructions.ts';
import type { AgentRequest } from '../run/request.ts';
import type { CliOutcome, NativeHandle, SessionName, Turn } from '../sessions/values.ts';
import type { Transport } from './transport.ts';

import { freezeEnv, validateTimeout } from '../run/settings.ts';
import { ValueError } from '../value-error.ts';
import { hasPermissionArgs, type Permissions, UnsupportedPermissions } from './permissions.ts';

/** Adapter for one agent CLI; the library runs the process, sessions, dry-run and Docker. */
export interface AgentCli {
  command: (request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext) => string[];
  /** The handle the CLI uses for a session started under `name`, or null when the CLI assigns its own. */
  handleForNew: (name: SessionName) => NativeHandle | null;
  readonly hookPoints: ReadonlySet<AgentCliHookPoint>;
  /** Pure translation of `hooks` into this CLI's native wiring; does no I/O. */
  hookWiring: (hooks: readonly AgentCliHook[], turn: Turn, workdir: string) => AgentCliHookWiring;
  /** Unique per CLI: it scopes session keys. */
  readonly name: string;
  /** The CLI's own name for `point`. */
  nativePoint: (point: AgentCliHookPoint) => string;
  /** Raises SessionHandleMissing when no handle can be determined. */
  parse: (stdout: string, turn: Turn, exitCode: number) => CliOutcome;
  readonly transport: Transport;
}

export interface AgentProfileInit {
  addDirs?: null | readonly string[];
  args?: readonly string[];
  context?: null | string;
  cwd?: null | string;
  env?: null | Readonly<Record<string, string>>;
  instructions?: Instructions | null | string;
  model?: null | string;
  /** Names the agent in observations, so a backend can tell several agents apart. */
  name?: null | string;
  permissions?: null | Permissions;
  reasoningEffort?: null | string;
  timeoutSec?: null | number;
}

/** An AgentCli that can enforce typed tool permissions. */
export interface PermissionAgentCli extends AgentCli {
  permissionArgs: (permissions: Permissions) => readonly string[];
}

/** Reusable agent instructions, CLI settings, and optional execution defaults. */
export class AgentProfile {
  readonly addDirs: null | readonly string[];
  readonly args: readonly string[];
  readonly cli: AgentCli;
  readonly context: null | string;
  readonly cwd: null | string;
  readonly env: null | Readonly<Record<string, string>>;
  readonly instructions: Instructions | null | string;
  readonly model: null | string;
  readonly name: null | string;
  readonly permissions: null | Permissions;
  readonly reasoningEffort: null | string;
  readonly timeoutSec: null | number;

  constructor(cli: AgentCli, init: AgentProfileInit = {}) {
    validateTimeout(init.timeoutSec);
    this.cli = cli;
    this.model = init.model ?? null;
    this.reasoningEffort = init.reasoningEffort ?? null;
    this.context = init.context ?? null;
    this.args = Object.freeze([...(init.args ?? [])]);
    this.name = init.name ?? null;
    this.instructions = init.instructions ?? null;
    this.cwd = init.cwd ?? null;
    this.addDirs = init.addDirs === null || init.addDirs === undefined ? null : Object.freeze([...init.addDirs]);
    this.permissions = init.permissions ?? null;
    this.env = freezeEnv(init.env);
    this.timeoutSec = init.timeoutSec ?? null;
    if (this.cwd !== null && !isAbsolute(this.cwd)) {
      throw new ValueError('profile cwd must be absolute');
    }
    if (this.addDirs?.some((path) => !isAbsolute(path)) === true) {
      throw new ValueError('profile addDirs must be absolute');
    }
    Object.freeze(this);
  }

  /** Overlay configured execution defaults, preserving an already opened workspace. */
  applyContext(defaults: AgentContext, options: { workspace?: boolean } = {}): AgentContext {
    const changes: { addDirs?: readonly string[]; cwd?: string; env?: Readonly<Record<string, string>>; timeoutSec?: number } = {};
    if (this.addDirs !== null) {
      changes.addDirs = this.addDirs;
    }
    if (this.env !== null) {
      changes.env = this.env;
    }
    if (this.timeoutSec !== null) {
      changes.timeoutSec = this.timeoutSec;
    }
    if (this.cwd !== null && options.workspace !== true) {
      changes.cwd = this.cwd;
    }
    return defaults.with(changes);
  }

  /** Fail before session registration if the CLI cannot enforce the policy. */
  validatePermissions(): void {
    if (this.permissions === null) {
      return;
    }
    if (hasPermissionArgs(this.args)) {
      throw new ValueError('permission arguments conflict with profile permissions');
    }
    if (!supportsPermissions(this.cli)) {
      throw new UnsupportedPermissions(`${this.cli.name} does not support tool permissions`);
    }
    this.cli.permissionArgs(this.permissions);
  }

  /** A copy with `changes` applied; a null clears a nullable setting. */
  with(changes: AgentProfileInit & { cli?: AgentCli }): AgentProfile {
    return new AgentProfile(changes.cli ?? this.cli, {
      addDirs: this.addDirs,
      args: this.args,
      context: this.context,
      cwd: this.cwd,
      env: this.env,
      instructions: this.instructions,
      model: this.model,
      name: this.name,
      permissions: this.permissions,
      reasoningEffort: this.reasoningEffort,
      timeoutSec: this.timeoutSec,
      ...changes,
    });
  }
}

/** Whether the CLI translates typed permissions into native arguments. */
export function supportsPermissions(cli: AgentCli): cli is PermissionAgentCli {
  return typeof (cli as Partial<PermissionAgentCli>).permissionArgs === 'function';
}
