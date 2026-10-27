import type { RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';
import type { CliOutcome, Turn } from '../sessions/values.ts';
import type { AgentProfile } from './base.ts';

import { AgentCliHookWiring } from '../hooks/agent-cli.ts';
import { type ProcessRunner, processRunner } from '../run/process.ts';

/** The injectable seam one Agent Run turn goes through; its outcome is the CLI's parsed stdout. */
export interface CliRunner {
  run: (profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) => Promise<CliOutcome>;
}

/** Runs any AgentCli as a process in the agent cwd; the process seam is injectable for testing. */
export class ProcessCliRunner implements CliRunner {
  readonly #process: ProcessRunner;

  constructor(processes: ProcessRunner = processRunner) {
    this.#process = processes;
  }

  async run(profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext): Promise<CliOutcome> {
    const argv = profile.cli.command(request, profile, turn, context);
    const wiring =
      context.agentCliHooks.length > 0
        ? profile.cli.hookWiring(context.agentCliHooks, turn, context.agent.cwd)
        : new AgentCliHookWiring();
    const env = context.agent.env === null && Object.keys(wiring.env).length === 0 ? undefined : { ...context.agent.env, ...wiring.env };
    const result = await this.#process.run(argv, {
      check: true,
      cwd: context.agent.cwd,
      env,
      timeoutSec: context.agent.timeoutSec ?? undefined,
    });
    return profile.cli.parse(result.stdout, turn, result.exitCode);
  }
}
