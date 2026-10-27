import { createHash } from 'node:crypto';

import type { RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';
import type { AgentProfile } from './base.ts';

import {
  AGENT_CLI_HOOK_POINTS,
  type AgentCliHook,
  AgentCliHookPoint,
  AgentCliHookWiring,
  ordered,
  shimCommand,
} from '../hooks/agent-cli.ts';
import { dumps, type Json, ordered as orderedObject } from '../json.ts';
import { CliOutcome, NativeHandle, Resume, type SessionName, Start, type Turn } from '../sessions/values.ts';
import { ValueError } from '../value-error.ts';
import { hasPermissionArgs, type Permissions } from './permissions.ts';
import { CopilotText, type Transcript, type Transport } from './transport.ts';

const DEFAULT_ARGS: readonly string[] = ['--autopilot', '--allow-all', '--no-ask-user'];
const BROAD_APPROVALS: readonly string[] = ['--allow-all', '--yolo', '--allow-all-tools'];

const EVENTS: Readonly<Record<AgentCliHookPoint, string>> = {
  [AgentCliHookPoint.AGENT_STOP]: 'agentStop',
  [AgentCliHookPoint.POST_TOOL]: 'postToolUse',
  [AgentCliHookPoint.PRE_COMPACT]: 'preCompact',
  [AgentCliHookPoint.PRE_TOOL]: 'preToolUse',
  [AgentCliHookPoint.PROMPT_SUBMITTED]: 'userPromptSubmitted',
  [AgentCliHookPoint.SESSION_END]: 'sessionEnd',
  [AgentCliHookPoint.SESSION_START]: 'sessionStart',
  [AgentCliHookPoint.SUBAGENT_START]: 'subagentStart',
  [AgentCliHookPoint.SUBAGENT_STOP]: 'subagentStop',
};

export interface CopilotCliOptions {
  transport?: Transport;
}

/** Copilot CLI adapter. */
export class CopilotCli {
  readonly hookPoints: ReadonlySet<AgentCliHookPoint> = new Set(AGENT_CLI_HOOK_POINTS);
  readonly name = 'copilot';
  readonly transport: Transport;
  readonly #args: readonly string[];

  constructor(args: readonly string[] = DEFAULT_ARGS, options: CopilotCliOptions = {}) {
    this.#args = Object.freeze([...args]);
    this.transport = options.transport ?? new CopilotText();
  }

  command(request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext): string[] {
    profile.validatePermissions();
    const cliArgs = profile.permissions === null ? this.#args : this.permissionArgs(profile.permissions);
    const dirs = context.agent.addDirs.flatMap((directory) => ['--add-dir', directory]);
    const settings: [string, null | string][] = [
      ['--model', profile.model],
      ['--reasoning-effort', profile.reasoningEffort],
      ['--context', profile.context],
    ];
    const runArgs = settings.flatMap(([flag, value]) => (value === null ? [] : [flag, value]));
    const sessionArgs =
      turn instanceof Start
        ? ['--name', request.name !== null && request.name !== '' ? request.name : turn.name.value]
        : [`--resume=${turn.handle.value}`];
    return [
      'copilot',
      '-p',
      request.prompt,
      ...this.transport.args,
      ...sessionArgs,
      ...cliArgs,
      ...runArgs,
      ...profile.args,
      ...dirs,
    ];
  }

  handleForNew(name: SessionName): NativeHandle {
    return new NativeHandle(this.name, name.value);
  }

  hookWiring(hooks: readonly AgentCliHook[], turn: Turn): AgentCliHookWiring {
    const events = new Map<string, Json[]>();
    for (const [point, hook] of ordered(hooks)) {
      const event = EVENTS[point];
      const entry = orderedObject([
        ['type', 'command'],
        ['bash', shimCommand(this.name, point, turn, hook)],
        ['timeoutSec', hook.timeoutSec],
      ]);
      events.set(event, [...(events.get(event) ?? []), entry]);
    }
    const digest = createHash('sha1').update(turn.name.value).digest('hex').slice(0, 8);
    const content = dumps(
      orderedObject([
        ['version', 1],
        ['hooks', Object.fromEntries(events)],
      ]),
      2,
    );
    return new AgentCliHookWiring({
      // Without it `copilot -p` silently skips repo hook files in an untrusted worktree.
      env: { GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS: 'true' },
      files: new Map([[`.github/hooks/loop-${digest}.json`, content]]),
      gitExcludes: ['.github/hooks/loop-*.json'],
    });
  }

  nativePoint(point: AgentCliHookPoint): string {
    return EVENTS[point];
  }

  parse(stdout: string, turn: Turn, exitCode: number): CliOutcome {
    const transcript: Transcript = this.transport.decode(stdout);
    const handle = turn instanceof Resume ? turn.handle : this.handleForNew(turn.name);
    // The process exit code wins; a 0 falls back to the stream's own exitCode, when the transport reports one.
    const code = exitCode !== 0 ? exitCode : (transcript.exitCode ?? 0);
    return new CliOutcome(transcript.text, handle, code);
  }

  /** Replace broad defaults with native tool approvals and deny patterns. */
  permissionArgs(permissions: Permissions): readonly string[] {
    const defaults = this.#args.filter((arg) => !BROAD_APPROVALS.includes(arg));
    if (hasPermissionArgs(defaults)) {
      throw new ValueError('CLI permission arguments conflict with profile permissions');
    }
    const allow =
      permissions.tools === 'allow-all'
        ? ['--allow-all-tools']
        : permissions.tools.flatMap((tool) => ['--allow-tool', tool]);
    const deny = permissions.denyTools.flatMap((tool) => ['--deny-tool', tool]);
    return [...defaults, ...allow, ...deny];
  }
}
