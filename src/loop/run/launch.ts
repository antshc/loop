import { quote } from '../shell.ts';
import { ValueError } from '../value-error.ts';
import { type AgentContext, AgentStartMode } from './context.ts';
import { type AgentRequest } from './request.ts';

/** In harness mode: launch in the harness, grant access to the worktree, and prefix the prompt with the cd instruction. */
export function applyStartMode(agent: AgentContext, request: AgentRequest): [AgentContext, AgentRequest] {
  if (agent.startMode === AgentStartMode.WORKTREE) {
    return [agent, request];
  }
  if (agent.harnessDir === null) {
    throw new ValueError("startMode 'harness' requires harnessDir");
  }
  const worktree = agent.cwd;
  const addDirs = agent.addDirs.includes(worktree) ? agent.addDirs : [...agent.addDirs, worktree];
  return [
    agent.with({ addDirs, cwd: agent.harnessDir }),
    request.with({ prompt: `${directoryInstruction(worktree)}\n\n${request.prompt}` }),
  ];
}

export function directoryInstruction(worktree: string): string {
  const quoted = quote(worktree);
  return (
    'This session was started in the harness repository only to load its context. ' +
    `All work on the target code happens in the git worktree at: ${worktree}\n` +
    `Before you inspect, edit, build, test or commit anything, change into it (\`cd ${quoted}\`). ` +
    'Shell state does not persist between tool calls, so run every command from that directory ' +
    `(start it with \`cd ${quoted} &&\`) or give it as the working directory. ` +
    'Keep using the harness context, but never treat the harness checkout as the target code ' +
    'and make no changes outside the worktree.'
  );
}
