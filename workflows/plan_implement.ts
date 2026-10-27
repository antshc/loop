// Self-contained Workflow: plan, implement, and report commits on one worktree without pushing.
// Run from the harness root through a user alias: `bun workflows/plan_implement.ts "<task>"`.
import { Command, CommanderError } from 'commander';
import { Container, inject, injectable } from 'inversify';
import { basename, join } from 'node:path';
import 'reflect-metadata';

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

/** A fresh Agent builder per run. */
export type AgentFactory = () => AgentBuilder;

/** Runs `git --no-pager <args>` in `path` and returns trimmed stdout; rejects when git fails. */
export type GitReader = (path: string, args: readonly string[]) => Promise<string>;
export interface Logger {
  error: (message: string) => void;
  info: (message: string) => void;
  warning: (message: string) => void;
}
/** Parses the command line into the task, or into the exit code to return for help or bad input. */
export interface TaskCommandLine {
  parse: (argv: readonly string[]) => number | string;
}
/** Writes text to an output stream. */
export type TextSink = (text: string) => unknown;
/** Eight lowercase hex characters naming the branch. */
export type UniqueId = () => string;

/** The directory the worktree root is derived from. */
export type WorkingDirectory = () => string;

/** The seams of this Workflow; the composition root is the only place that binds implementations. */
export const TOKENS = {
  agent: Symbol.for('plan_implement.AgentFactory'),
  commandLine: Symbol.for('plan_implement.TaskCommandLine'),
  cwd: Symbol.for('plan_implement.WorkingDirectory'),
  logger: Symbol.for('plan_implement.Logger'),
  readGit: Symbol.for('plan_implement.GitReader'),
  stderr: Symbol.for('plan_implement.ErrorStream'),
  stdout: Symbol.for('plan_implement.OutputStream'),
  uniqueId: Symbol.for('plan_implement.UniqueId'),
  workflow: Symbol.for('plan_implement.PlanImplementWorkflow'),
} as const;

/** Each member rebinds the token of the shipped implementation of the same name. */
export interface PlanImplementDependencies {
  agent?: AgentFactory;
  cwd?: WorkingDirectory;
  logger?: Logger;
  readGit?: GitReader;
  /** Writes usage and error text for bad input. */
  stderr?: TextSink;
  /** Writes help requested on the command line. */
  stdout?: TextSink;
  uniqueId?: UniqueId;
}

@injectable()
class CommanderTaskCommandLine implements TaskCommandLine {
  constructor(
    @inject(TOKENS.stdout) private readonly stdout: TextSink,
    @inject(TOKENS.stderr) private readonly stderr: TextSink,
  ) {}

  parse(argv: readonly string[]): number | string {
    let task = '';
    const program = new Command('plan_implement')
      .description('Plan, then implement a task on one worktree without pushing.')
      .argument('<task>', 'the task to plan and implement')
      .configureOutput({ writeErr: this.stderr, writeOut: this.stdout })
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
}

@injectable()
class PlanImplementWorkflow {
  constructor(
    @inject(TOKENS.commandLine) private readonly commandLine: TaskCommandLine,
    @inject(TOKENS.agent) private readonly agent: AgentFactory,
    @inject(TOKENS.cwd) private readonly cwd: WorkingDirectory,
    @inject(TOKENS.logger) private readonly logger: Logger,
    @inject(TOKENS.readGit) private readonly readGit: GitReader,
    @inject(TOKENS.uniqueId) private readonly uniqueId: UniqueId,
  ) {}

  async run(argv: readonly string[]): Promise<number> {
    const { logger, readGit } = this;
    const parsed = this.commandLine.parse(argv);
    if (typeof parsed === 'number') {
      return parsed;
    }
    const branch = `loop/run-${this.uniqueId()}`;
    logger.info(`branch ${branch}`);
    const options = new GitOptions({
      rootPath: join('workspace', `${basename(this.cwd())}.worktrees`),
      strategy: new BranchStrategy(branch, 'origin/main'),
    });

    return this.agent()
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
}

/** Without a configured host logger only warnings and errors reach stderr, as the message alone. */
@injectable()
class StderrLogger implements Logger {
  error(message: string): void {
    writeStderr(message);
  }

  info(): void {
    // Informational records need a configured host logger.
  }

  warning(message: string): void {
    writeStderr(message);
  }
}

/** The composition root: binds the shipped implementations, then rebinds each token that has an override. */
export function compose(overrides: PlanImplementDependencies = {}): Container {
  const container = new Container();
  container.bind<AgentFactory>(TOKENS.agent).toConstantValue(() => Agent());
  container.bind<TaskCommandLine>(TOKENS.commandLine).to(CommanderTaskCommandLine).inSingletonScope();
  container.bind<WorkingDirectory>(TOKENS.cwd).toConstantValue(() => process.cwd());
  container.bind<Logger>(TOKENS.logger).to(StderrLogger).inSingletonScope();
  container.bind<GitReader>(TOKENS.readGit).toConstantValue(gitOutput);
  container.bind<TextSink>(TOKENS.stderr).toConstantValue(writeStderr);
  container.bind<TextSink>(TOKENS.stdout).toConstantValue(writeStdout);
  container.bind<UniqueId>(TOKENS.uniqueId).toConstantValue(shortId);
  container.bind<PlanImplementWorkflow>(TOKENS.workflow).to(PlanImplementWorkflow).inSingletonScope();

  for (const [name, override] of Object.entries(overrides) as [keyof PlanImplementDependencies, unknown][]) {
    if (override !== undefined) {
      container.rebind(TOKENS[name]).toConstantValue(override);
    }
  }
  return container;
}

/** Plans and implements a task in one worktree; resolves 0 on success, 1 on step failure, or 2 on bad input. */
export async function main(argv: readonly string[], dependencies: PlanImplementDependencies = {}): Promise<number> {
  return await compose(dependencies).get<PlanImplementWorkflow>(TOKENS.workflow).run(argv);
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
