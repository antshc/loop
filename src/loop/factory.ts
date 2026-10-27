import type { GitService } from './git/service.ts';
import type { AgentOptions } from './run/context.ts';

import { AgentBuilder } from './builder.ts';
import { ProcessCliRunner } from './clis/runner.ts';
import { DockerRuntime } from './docker/runtime.ts';
import { LoggingDocker, LoggingRunner } from './dryrun/adapters.ts';
import { DirectoryGit, type FakeCliRunner } from './dryrun/fakes.ts';
import { AgentContext } from './run/context.ts';

/** Composition root: register private default dependencies and return the builder. */
export function Agent(options: AgentOptions | null = null): AgentBuilder {
  const agentContext = applyOptions(new AgentContext(), options);
  if (options?.dryRun === true) {
    return new AgentBuilder(new LoggingRunner(), null, new LoggingDocker(new DockerRuntime()), agentContext, null, {
      dryRun: true,
    });
  }
  return new AgentBuilder(new ProcessCliRunner(), null, new DockerRuntime(), agentContext);
}

/** A builder that runs `runner` instead of a CLI in a temporary directory instead of a worktree, for end-to-end runs in isolation. */
export function fakeAgent(
  runner: FakeCliRunner,
  git: GitService | null = null,
  options: AgentOptions | null = null,
): AgentBuilder {
  return new AgentBuilder(runner, git ?? new DirectoryGit(), new DockerRuntime(), applyOptions(new AgentContext(), options));
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
