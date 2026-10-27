import type { AgentCliHook } from '../hooks/agent-cli.ts';
import type { RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';
import type { AgentProfile } from './base.ts';

import { AGENT_CLI_HOOK_POINTS, AgentCliHookPoint, AgentCliHookWiring, ordered, shimCommand } from '../hooks/agent-cli.ts';
import { dumps } from '../json.ts';
import {
  CliOutcome,
  NativeHandle,
  Resume,
  SessionHandleMissing,
  type Turn,
} from '../sessions/values.ts';
import { CodexJson, type Transport } from './transport.ts';

const DEFAULT_ARGS: readonly string[] = ['--sandbox', 'workspace-write'];
const SESSION_END_MAX_TIMEOUT = 3;

const EVENTS: Readonly<Record<AgentCliHookPoint, string>> = {
  [AgentCliHookPoint.AGENT_STOP]: 'Stop',
  [AgentCliHookPoint.POST_TOOL]: 'PostToolUse',
  [AgentCliHookPoint.PRE_COMPACT]: 'PreCompact',
  [AgentCliHookPoint.PRE_TOOL]: 'PreToolUse',
  [AgentCliHookPoint.PROMPT_SUBMITTED]: 'UserPromptSubmit',
  [AgentCliHookPoint.SESSION_END]: 'SessionEnd',
  [AgentCliHookPoint.SESSION_START]: 'SessionStart',
  [AgentCliHookPoint.SUBAGENT_START]: 'SubagentStart',
  [AgentCliHookPoint.SUBAGENT_STOP]: 'SubagentStop',
};

export interface CodexCliOptions {
  hookTrustBypass?: boolean;
}

/** Codex CLI adapter; Codex assigns its own thread id. */
export class CodexCli {
  readonly hookPoints: ReadonlySet<AgentCliHookPoint>;
  readonly name = 'codex';
  readonly transport: Transport = new CodexJson();
  readonly #args: readonly string[];

  constructor(args: readonly string[] = DEFAULT_ARGS, options: CodexCliOptions = {}) {
    this.#args = Object.freeze([...args]);
    // Without the bypass Codex silently skips the hooks, so none are claimed.
    this.hookPoints = new Set(options.hookTrustBypass === true ? AGENT_CLI_HOOK_POINTS : []);
  }

  command(request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext): string[] {
    profile.validatePermissions();
    const dirs = context.agent.addDirs.flatMap((directory) => ['--add-dir', directory]);
    const runArgs: string[] = [];
    if (profile.model !== null) {
      runArgs.push('--model', profile.model);
    }
    if (profile.reasoningEffort !== null) {
      runArgs.push('-c', `model_reasoning_effort=${profile.reasoningEffort}`);
    }
    const head = ['codex', 'exec', ...this.transport.args, ...this.#args, ...runArgs, ...profile.args, ...dirs];
    if (context.agentCliHooks.length > 0) {
      head.push(...this.hookWiring(context.agentCliHooks, turn).args);
    }
    if (turn instanceof Resume) {
      return [...head.slice(0, 2), 'resume', turn.handle.value, ...head.slice(2), request.prompt];
    }
    return [...head, request.prompt];
  }

  handleForNew(): null {
    return null;
  }

  hookWiring(hooks: readonly AgentCliHook[], turn: Turn): AgentCliHookWiring {
    const events = new Map<string, string[]>();
    for (const [point, hook] of ordered(hooks)) {
      const timeout = point === AgentCliHookPoint.SESSION_END ? Math.min(hook.timeoutSec, SESSION_END_MAX_TIMEOUT) : hook.timeoutSec;
      const command = dumps(shimCommand(this.name, point, turn, hook));
      const matcher = point === AgentCliHookPoint.SESSION_START ? 'matcher="startup|resume",' : '';
      const event = EVENTS[point];
      events.set(event, [
        ...(events.get(event) ?? []),
        `{${matcher}hooks=[{type="command",command=${command},timeout=${String(timeout)}}]}`,
      ]);
    }
    const args = [...events].flatMap(([event, entries]) => ['-c', `hooks.${event}=[${entries.join(',')}]`]);
    return new AgentCliHookWiring({ args: [...args, '--dangerously-bypass-hook-trust'] });
  }

  nativePoint(point: AgentCliHookPoint): string {
    return EVENTS[point];
  }

  parse(stdout: string, turn: Turn, exitCode: number): CliOutcome {
    const transcript = this.transport.decode(stdout);
    if (transcript.handleValue !== null) {
      return new CliOutcome(transcript.text, new NativeHandle(this.name, transcript.handleValue), exitCode);
    }
    if (turn instanceof Resume) {
      return new CliOutcome(transcript.text, turn.handle, exitCode);
    }
    throw new SessionHandleMissing('codex output had no thread.started event');
  }
}
