import { join } from 'node:path';

import type { AgentClient } from './agents/client.ts';
import type { GitService } from './git/service.ts';
import type { AgentOptions } from './run/context.ts';
import type { SessionName } from './sessions/values.ts';

import { AgentBuilder } from './builder.ts';
import { DEFAULT } from './clis/defaults.ts';
import { ProcessCliRunner } from './clis/runner.ts';
import { DockerRuntime } from './docker/runtime.ts';
import { LoggingDocker, LoggingGitCli, LoggingRunner } from './dryrun/adapters.ts';
import { DirectoryGit, type FakeCliRunner } from './dryrun/fakes.ts';
import { GitCli } from './git/git-cli.ts';
import { GitRuntime } from './git/runtime.ts';
import { GitOptions } from './git/service.ts';
import { BranchStrategy, MergeToHeadStrategy } from './git/strategies.ts';
import { AgentContext } from './run/context.ts';

/** Composition root: register private default dependencies and return the builder. */
export function Agent(options: AgentOptions | null = null): AgentBuilder {
  const agentContext = applyOptions(new AgentContext(), options);
  if (options?.dryRun === true) {
    return new AgentBuilder(
      new LoggingRunner(),
      new GitRuntime(new LoggingGitCli()),
      new LoggingDocker(new DockerRuntime()),
      agentContext,
      null,
      { dryRun: true },
    );
  }
  return new AgentBuilder(new ProcessCliRunner(), new GitRuntime(new GitCli()), new DockerRuntime(), agentContext);
}

/** A builder that runs `runner` instead of a CLI in a temporary directory instead of a worktree, for end-to-end runs in isolation. */
export function fakeAgent(
  runner: FakeCliRunner,
  git: GitService | null = null,
  options: AgentOptions | null = null,
): AgentBuilder {
  return new AgentBuilder(runner, git ?? new DirectoryGit(), new DockerRuntime(), applyOptions(new AgentContext(), options));
}

/**
 * One worktree-isolated agent for `workspace/<repo>`, with worktrees in `workspace/<repo>.worktrees`;
 * a `session` continues across runs. Without a `branch` the run merges into HEAD on success.
 */
export function repoAgent(repo: string, branch: null | string = null, session: null | SessionName = null): AgentClient {
  const options = new GitOptions({
    repositoryPath: join('workspace', repo),
    rootPath: join('workspace', `${repo}.worktrees`),
    strategy: branch === null || branch === '' ? new MergeToHeadStrategy() : new BranchStrategy(branch),
  });
  const builder = Agent().withGit(options).withDocker();
  return session === null ? builder.create() : builder.withSession().create(DEFAULT, session);
}

function applyOptions(context: AgentContext, options: AgentOptions | null): AgentContext {
  let result = context;
  if (options?.dockerImage !== undefined) {
    result = result.with({ dockerImage: options.dockerImage });
  }
  if (options?.startMode !== undefined) {
    result = result.with({ harnessDir: options.harnessDir ?? null, startMode: options.startMode });
  }
  return result;
}
