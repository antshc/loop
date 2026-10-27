// Self-contained Workflow: plan, implement, and report commits on one worktree without pushing.
// Run from the harness root through a user alias: `bun workflows/plan_implement.ts "<task>"`.
import { Command, CommanderError } from 'commander';
import { basename, join } from 'node:path';

import {
  Agent,
  type AgentBuilder,
  AgentProfile,
  AgentRequest,
  type AgentWorkspace,
  BranchStrategy,
  copilot,
  enforceRuntime,
  GitOptions,
  ProcessFailedError,
  Role,
  runEntrypoint,
  ValueError,
} from '../src/loop/index.ts';

export const PLAN_MODEL = 'claude-opus-5.5';
export const PLAN_EFFORT = 'max';
export const IMPLEMENT_MODEL = 'claude-sonnet-5.5';
export const IMPLEMENT_EFFORT = 'high';

export const PLAN_INSTRUCTIONS = `You are planning a coding task on this repository. Do not write or change any code.

Write a concise, actionable implementation plan for another agent to follow.

End your final message with exactly one of these tags, with nothing after it:
<completed>Success</completed>
<completed>Failure: <why></completed>
`;

export const IMPLEMENT_INSTRUCTIONS = `You are implementing a coding task on this repository, following the plan above.

Implement the plan. Commit your work with git. Do not push.

End your final message with exactly one of these tags, with nothing after it:
<completed>Success</completed>
<completed>Failure: <why></completed>
`;

/** The Agent profiles this Workflow owns: one per step, each with its own model and effort. */
export const planner = new AgentProfile(copilot, { model: PLAN_MODEL, name: Role.PLANNER, reasoningEffort: PLAN_EFFORT });
export const implementer = new AgentProfile(copilot, {
  model: IMPLEMENT_MODEL,
  name: Role.DEVELOPER,
  reasoningEffort: IMPLEMENT_EFFORT,
});

export interface Logger {
  error: (message: string) => void;
  info: (message: string) => void;
  warning: (message: string) => void;
}

/** Each member replaces the shipped implementation of the same name. */
export interface PlanImplementDependencies {
  /** A fresh Agent builder per run. */
  agent?: () => AgentBuilder;
  /** The directory the worktree root is derived from. */
  cwd?: () => string;
  logger?: Logger;
  /** Runs `git --no-pager <args>` in `path` and returns trimmed stdout; rejects when git fails. */
  readGit?: (path: string, args: readonly string[]) => Promise<string>;
  /** Writes usage and error text for bad input. */
  stderr?: (text: string) => unknown;
  /** Writes help requested on the command line. */
  stdout?: (text: string) => unknown;
  /** Eight lowercase hex characters naming the branch. */
  uniqueId?: () => string;
}

/** Without a configured host logger only warnings and errors reach stderr, as the message alone. */
const defaultLogger: Logger = {
  error: writeStderr,
  info: () => undefined,
  warning: writeStderr,
};

/** Plans and implements a task in one worktree; resolves 0 on success, 1 on step failure, or 2 on bad input. */
export async function main(argv: readonly string[], dependencies: PlanImplementDependencies = {}): Promise<number> {
  const {
    agent = Agent,
    cwd = () => process.cwd(),
    logger = defaultLogger,
    readGit = gitOutput,
    stderr = writeStderr,
    stdout = writeStdout,
    uniqueId = shortId,
  } = dependencies;
  const parsed = parseCommandLine(argv, stdout, stderr);
  if (typeof parsed === 'number') {
    return parsed;
  }
  const branch = `loop/run-${uniqueId()}`;
  logger.info(`branch ${branch}`);
  const options = new GitOptions({
    rootPath: join('workspace', `${basename(cwd())}.worktrees`),
    strategy: new BranchStrategy(branch, 'origin/main'),
  });

  return agent()
    .withGit(options)
    .open(async (worktree) => {
      const plan = await runStep(worktree, planner, `${parsed}\n\n${PLAN_INSTRUCTIONS}`, logger);
      if (plan === null || plan === '') {
        logger.error('planning run failed: no usable plan');
        return 1;
      }

      const headBefore = await readGit(worktree.path, ['rev-parse', 'HEAD']);
      const result = await runStep(worktree, implementer, `${plan}\n\n${IMPLEMENT_INSTRUCTIONS}`, logger);
      const commits = await readGit(worktree.path, ['log', '--reverse', '--format=%H', `${headBefore}..HEAD`]);
      logger.info(`commits: ${commits.split('\n').filter((line) => line !== '').join(', ') || 'none'}`);
      if (result === null) {
        logger.error('implementing run failed');
        return 1;
      }
      return 0;
    });
}

/** Returns the text before a success tag; throws ValueError for missing or unsuccessful tags. */
export function responseBody(text: string): string {
  const match = /<completed>(.*?)<\/completed>/s.exec(text);
  if (match === null) {
    throw new ValueError('no <completed> tag in agent output');
  }
  const status = (match[1] ?? '').trim();
  if (status.toLowerCase() !== 'success') {
    throw new ValueError(`agent reported: ${status}`);
  }
  return text.slice(0, match.index).trim();
}

async function gitOutput(path: string, args: readonly string[]): Promise<string> {
  const child = Bun.spawn(['git', '--no-pager', ...args], { cwd: path, stderr: 'pipe', stdout: 'pipe' });
  const [stdout, stderr, exitCode] = await Promise.all([child.stdout.text(), child.stderr.text(), child.exited]);
  if (exitCode !== 0) {
    throw new Error(`git ${args.join(' ')} failed with exit status ${String(exitCode)}: ${stderr.trim()}`);
  }
  return stdout.trim();
}

/** The task, or the exit code to return when the command line is help or bad input. */
function parseCommandLine(
  argv: readonly string[],
  stdout: (text: string) => unknown,
  stderr: (text: string) => unknown,
): number | string {
  let task = '';
  const program = new Command('plan_implement')
    .description('Plan, then implement a task on one worktree without pushing.')
    .argument('<task>', 'the task to plan and implement')
    .configureOutput({ writeErr: stderr, writeOut: stdout })
    .showHelpAfterError()
    .exitOverride()
    .action((value: string) => {
      task = value;
    });
  try {
    program.parse([...argv], { from: 'user' });
  } catch (error) {
    if (error instanceof CommanderError) {
      return error.exitCode === 0 ? 0 : 2;
    }
    throw error;
  }
  return task;
}

/** Runs a fresh session and returns its response body, or logs the failure and returns null. */
async function runStep(worktree: AgentWorkspace, profile: AgentProfile, prompt: string, logger: Logger): Promise<null | string> {
  let output: string;
  try {
    output = (await worktree.agent(profile).run(new AgentRequest(prompt))).output;
  } catch (error) {
    if (error instanceof ProcessFailedError) {
      logger.error(`agent command failed: ${error.message}; stderr: ${error.stderr}`);
      return null;
    }
    throw error;
  }
  try {
    return responseBody(output);
  } catch (error) {
    if (error instanceof ValueError) {
      logger.error(error.message);
      return null;
    }
    throw error;
  }
}

function shortId(): string {
  return crypto.randomUUID().replaceAll('-', '').slice(0, 8);
}

function writeStderr(text: string): void {
  process.stderr.write(text.endsWith('\n') ? text : `${text}\n`);
}

function writeStdout(text: string): void {
  process.stdout.write(text);
}

if (import.meta.main) {
  enforceRuntime();
  await runEntrypoint(async () => {
    process.exitCode = await main(process.argv.slice(2));
  });
}
