import type { AgentProfile } from './clis/base.ts';
import type { CliRunner } from './clis/runner.ts';
import type { DockerService } from './docker/runtime.ts';
import type { GitService } from './git/service.ts';
import type { RunObserver } from './observe/values.ts';
import type { SessionStore } from './sessions/store.ts';

import { type AgentClient, CliAgentClient } from './agents/client.ts';
import { DockerAgent, GitAgent } from './agents/wrappers.ts';
import { CopilotLogReader } from './clis/copilot-log.ts';
import { DEFAULT } from './clis/defaults.ts';
import { ObservingCliRunner, type SessionLogReader } from './clis/observing.ts';
import { GitOptions } from './git/service.ts';
import {
  type AgentCliHook,
  SessionEndAgentCliHook,
  SessionStartAgentCliHook,
  UnsupportedAgentCliHookPoint,
} from './hooks/agent-cli.ts';
import { AgentContext, RunContext } from './run/context.ts';
import { MemorySessionStore } from './sessions/store.ts';
import { type SessionName } from './sessions/values.ts';
import { ValueError } from './value-error.ts';

export interface AgentBuilderOptions {
  dryRun?: boolean;
}

export interface WithObserverOptions {
  /** Defaults to the Copilot session-log reader. */
  readers?: readonly SessionLogReader[];
}

export class AgentBuilder {
  #defaults: RunContext;
  readonly #docker: DockerService;
  readonly #dryRun: boolean;
  readonly #git: GitService | null;
  #gitOptions: GitOptions | null = null;
  #observed = false;
  #runner: CliRunner;
  readonly #sessions: SessionStore;
  #useDocker = false;
  #useGit = false;
  #useSession = false;

  /** The seams are injectable: a runner, a git service (null until worktrees are available), and a docker service. */
  constructor(
    runner: CliRunner,
    git: GitService | null,
    docker: DockerService,
    agentContext: AgentContext | null = null,
    sessions: null | SessionStore = null,
    options: AgentBuilderOptions = {},
  ) {
    this.#runner = runner;
    this.#git = git;
    this.#docker = docker;
    this.#sessions = sessions ?? new MemorySessionStore();
    this.#defaults = new RunContext(agentContext ?? new AgentContext());
    this.#dryRun = options.dryRun === true;
  }

  /** One-shot client: a separate git lifecycle per run(). */
  create(profile: AgentProfile = DEFAULT, session: null | SessionName = null): AgentClient {
    this.#requireSessionEnabled(session);
    let client = this.#stack(profile, this.#defaults, session);
    if (this.#useGit) {
      client = new GitAgent(client, this.#requireGit(), this.#gitOptions, profile.applyContext(this.#defaults.agent));
    }
    return client;
  }

  /** Observe-only native CLI hooks for every run; a string runs at session start and end. */
  withAgentCliHooks(...hooks: (AgentCliHook | string)[]): this {
    if (hooks.length === 0) {
      throw new ValueError('withAgentCliHooks() needs at least one hook');
    }
    const resolved = hooks.flatMap((item) =>
      typeof item === 'string' ? [new SessionStartAgentCliHook(item), new SessionEndAgentCliHook(item)] : [item],
    );
    this.#defaults = this.#defaults.with({ agentCliHooks: resolved });
    return this;
  }

  withDocker(): this {
    this.#useDocker = true;
    return this;
  }

  withGit(options: GitOptions | null = null): this {
    this.#requireGit();
    this.#useGit = true;
    this.#gitOptions = options ?? new GitOptions();
    return this;
  }

  /** Report every Agent Run to `observer`; `readers` default to the Copilot session-log reader. */
  withObserver(observer: RunObserver, options: WithObserverOptions = {}): this {
    if (this.#observed) {
      throw new ValueError('withObserver() was already called');
    }
    this.#observed = true;
    this.#runner = new ObservingCliRunner(this.#runner, observer, options.readers ?? [new CopilotLogReader()]);
    return this;
  }

  /** Continue sessions across runs; without it every run is stateless. */
  withSession(): this {
    this.#useSession = true;
    return this;
  }

  #requireGit(): GitService {
    if (this.#git === null) {
      throw new ValueError('withGit() needs a GitService');
    }
    return this.#git;
  }

  #requireSessionEnabled(session: null | SessionName): void {
    if (session !== null && !this.#useSession) {
      throw new ValueError('session requires withSession()');
    }
  }

  #stack(profile: AgentProfile, defaults: RunContext, session: null | SessionName): AgentClient {
    const resolved = defaults.with({ agent: profile.applyContext(defaults.agent) });
    this.#validateAgentCliHooks(profile, resolved);
    let client: AgentClient = new CliAgentClient(this.#runner, profile, resolved, {
      dryRun: this.#dryRun,
      session,
      store: this.#useSession ? this.#sessions : null,
    });
    if (this.#useDocker) {
      client = new DockerAgent(client, this.#docker, resolved.agent);
    }
    return client;
  }

  #validateAgentCliHooks(profile: AgentProfile, defaults: RunContext): void {
    const missing = new Set(defaults.agentCliHooks.map((hook) => hook.point));
    for (const point of profile.cli.hookPoints) {
      missing.delete(point);
    }
    if (missing.size > 0) {
      throw new UnsupportedAgentCliHookPoint(`${profile.cli.name} cannot fire ${[...missing].sort().join(', ')}`);
    }
  }
}
