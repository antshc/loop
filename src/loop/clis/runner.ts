import { appendFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

import type { RunContext } from '../run/context.ts';
import type { AgentRequest } from '../run/request.ts';
import type { CliOutcome, Turn } from '../sessions/values.ts';
import type { AgentProfile } from './base.ts';

import { getLogger } from '../diagnostics.ts';
import { AgentCliHookWiring } from '../hooks/agent-cli.ts';
import { type ProcessRunner, processRunner } from '../run/process.ts';

const logger = getLogger('loop.clis.runner');

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
    logger.info(`agent run started: cli=${profile.cli.name} session=${turn.name.value} cwd=${context.agent.cwd}`);
    logger.debug(`agent command: ${argv.join(' ')}`);
    const result = await this.#withHookFiles(wiring, context.agent.cwd, () =>
      this.#process.run(argv, {
        check: true,
        cwd: context.agent.cwd,
        env,
        timeoutSec: context.agent.timeoutSec ?? undefined,
      }),
    );
    logger.info(`agent run finished: cli=${profile.cli.name} exit_code=${String(result.exitCode)}`);
    return profile.cli.parse(result.stdout, turn, result.exitCode);
  }

  /** Appends the patterns missing from the repository's `info/exclude`; a directory without git is left alone. */
  async #exclude(cwd: string, patterns: readonly string[]): Promise<void> {
    const found = await this.#process.run(['git', '-C', cwd, 'rev-parse', '--git-path', 'info/exclude']);
    if (found.exitCode !== 0 || found.stdout.trim() === '') {
      return;
    }
    const exclude = resolve(cwd, found.stdout.trim());
    const existing = await readFile(exclude, 'utf8').then(
      (text) => text.split('\n'),
      (): string[] => [],
    );
    const missing = patterns.filter((pattern) => !existing.includes(pattern));
    if (missing.length > 0) {
      await mkdir(dirname(exclude), { recursive: true });
      await appendFile(exclude, missing.map((pattern) => `${pattern}\n`).join(''));
    }
  }

  /** Writes the wiring's files for the duration of `action` and removes them after every run, even a failed or cancelled one. */
  async #withHookFiles<T>(wiring: AgentCliHookWiring, cwd: string, action: () => Promise<T>): Promise<T> {
    const written: string[] = [];
    try {
      for (const [relative, content] of wiring.files) {
        const path = resolve(cwd, relative);
        await mkdir(dirname(path), { recursive: true });
        await writeFile(path, content);
        written.push(path);
        logger.debug(`hook file written: ${path}`);
      }
      if (wiring.gitExcludes.length > 0) {
        await this.#exclude(cwd, wiring.gitExcludes);
      }
      return await action();
    } finally {
      await Promise.all(written.map((path) => rm(path, { force: true })));
    }
  }
}
