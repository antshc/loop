import type { AgentProfile } from '../clis/base.ts';
import type { CliRunner } from '../clis/runner.ts';
import type { SessionStore } from '../sessions/store.ts';

import { AgentContext, RunContext } from '../run/context.ts';
import { type Instructions, resolveInstructions } from '../run/instructions.ts';
import { applyStartMode } from '../run/launch.ts';
import { type AgentRequest, AgentResult } from '../run/request.ts';
import { type NativeHandle, Resume, SessionCliMismatch, SessionName, Start, type Turn } from '../sessions/values.ts';
import { ValueError } from '../value-error.ts';

/** Stable public interface, regardless of enabled wrappers. */
export interface AgentClient {
  close: () => Promise<void>;
  run: (request: AgentRequest, context?: AgentContext) => Promise<AgentResult>;
}

export interface CliAgentClientOptions {
  dryRun?: boolean;
  session?: null | SessionName;
  store?: null | SessionStore;
  workspace?: boolean;
}

/** Core agent bound to one CLI profile; decides start or resume, then delegates to its runner. */
export class CliAgentClient implements AgentClient {
  readonly #defaults: RunContext;
  readonly #dryRun: boolean;
  #instructions: null | Promise<null | string> = null;
  readonly #profile: AgentProfile;
  readonly #runner: CliRunner;
  readonly #session: null | SessionName;
  readonly #store: null | SessionStore;

  constructor(
    runner: CliRunner,
    profile: AgentProfile,
    defaults: RunContext = new RunContext(),
    options: CliAgentClientOptions = {},
  ) {
    const store = options.store ?? null;
    if (options.session !== undefined && options.session !== null && store === null) {
      throw new ValueError('session requires withSession()');
    }
    profile.validatePermissions();
    this.#runner = runner;
    this.#profile = profile;
    this.#defaults = defaults.with({ agent: profile.applyContext(defaults.agent, { workspace: options.workspace === true }) });
    this.#store = store;
    this.#session = options.session ?? (store === null ? null : SessionName.new());
    this.#dryRun = options.dryRun === true;
  }

  close(): Promise<void> {
    return Promise.resolve();
  }

  async run(request: AgentRequest, context?: AgentContext): Promise<AgentResult> {
    if (request.name !== null && this.#store !== null) {
      throw new ValueError('request name requires a stateless client');
    }
    let runContext = context === undefined ? this.#defaults : this.#defaults.with({ agent: context });
    const instructions = await this.#snapshotInstructions();
    if (instructions !== null) {
      request = request.with({ prompt: `## Agent instructions\n${instructions}\n\n## Task\n${request.prompt}` });
    }
    const [agent, launched] = applyStartMode(runContext.agent, request);
    runContext = runContext.with({ agent });
    const turn = await this.#turn();
    const outcome = await this.#runner.run(this.#profile, launched, turn, runContext);
    await this.#store?.save(turn.name, outcome.handle);
    return new AgentResult(outcome.output, turn.name, outcome.exitCode);
  }

  /** Loads the profile's instructions on first use and keeps that snapshot; a failed load is retried. */
  #snapshotInstructions(): Promise<null | string> {
    const source: Instructions | null | string = this.#profile.instructions;
    this.#instructions ??= resolveInstructions(source, { dryRun: this.#dryRun }).catch((error: unknown) => {
      this.#instructions = null;
      throw error;
    });
    return this.#instructions;
  }

  async #turn(): Promise<Turn> {
    if (this.#store === null || this.#session === null) {
      return new Start(SessionName.new());
    }
    const cli = this.#profile.cli;
    const handle: NativeHandle | null = await this.#store.get(this.#session, cli.name);
    if (handle !== null) {
      if (handle.cli !== cli.name) {
        throw new SessionCliMismatch(`handle for ${handle.cli} found under ${cli.name}`);
      }
      return new Resume(this.#session, handle);
    }
    const newHandle = cli.handleForNew(this.#session);
    if (newHandle !== null) {
      // Saved before the run so a retry resumes instead of colliding on the name.
      await this.#store.save(this.#session, newHandle);
    }
    return new Start(this.#session);
  }
}
