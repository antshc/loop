import { join } from 'node:path';

import type { AgentCli, AgentProfile } from '../clis/base.ts';
import type { CliRunner } from '../clis/runner.ts';
import type { DockerService } from '../docker/runtime.ts';
import type { AgentContext, RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';

import { ordered } from '../hooks/agent-cli.ts';
import { CliOutcome, NativeHandle, Resume, type Turn } from '../sessions/values.ts';

export type Log = (message: string) => void;

/** Dry-run DockerService: delegates to the stub and logs the configured image. */
export class LoggingDocker implements DockerService {
  readonly #inner: DockerService;
  readonly #log: Log;

  constructor(inner: DockerService, log: Log = dryRunLog) {
    this.#inner = inner;
    this.#log = log;
  }

  async configure(context: AgentContext): Promise<AgentContext> {
    const configured = await this.#inner.configure(context);
    this.#log(`docker image=${String(configured.dockerImage)}`);
    return configured;
  }
}

/** Dry-run CliRunner: logs the command instead of running it. */
export class LoggingRunner implements CliRunner {
  readonly #log: Log;

  constructor(log: Log = dryRunLog) {
    this.#log = log;
  }

  run(profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext): Promise<CliOutcome> {
    const command = profile.cli.command(request, profile, turn, context);
    const output = `cwd=${context.agent.cwd} cmd=${command.join(' ')}`;
    this.#log(output);
    this.#logHooks(profile, turn, context);
    return Promise.resolve(new CliOutcome(output, handle(profile.cli, turn), 0));
  }

  #logHooks(profile: AgentProfile, turn: Turn, context: RunContext): void {
    if (context.agentCliHooks.length === 0) {
      return;
    }
    const cli = profile.cli;
    for (const [point, hook] of ordered(context.agentCliHooks)) {
      this.#log(`hook ${cli.name} ${point} -> ${cli.nativePoint(point)}: ${hook.command}`);
    }
    const wiring = cli.hookWiring(context.agentCliHooks, turn, context.agent.cwd);
    for (const path of wiring.files.keys()) {
      this.#log(`hook file ${join(context.agent.cwd, path)}`);
    }
    if (wiring.args.length > 0) {
      this.#log(`hook args ${wiring.args.join(' ')}`);
    }
    if (Object.keys(wiring.env).length > 0) {
      this.#log(`hook env ${JSON.stringify(wiring.env)}`);
    }
  }
}

function dryRunLog(message: string): void {
  console.log(`[dry-run] ${message}`);
}

function handle(cli: AgentCli, turn: Turn): NativeHandle {
  if (turn instanceof Resume) {
    return turn.handle;
  }
  return cli.handleForNew(turn.name) ?? new NativeHandle(cli.name, `dry-${crypto.randomUUID().replaceAll('-', '')}`);
}
