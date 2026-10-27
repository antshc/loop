import { isAbsolute, join } from 'node:path';

import type { GitCli } from './git-cli.ts';
import type { GitOptions, GitService } from './service.ts';

import { LoopHookPoint } from '../hooks/loop.ts';

/** The run scope: delegates to the options' strategy, then runs the run-finished hooks after a successful body. */
export class GitRuntime implements GitService {
  readonly #git: GitCli;

  constructor(git: GitCli) {
    this.#git = git;
  }

  async open<T>(cwd: string, options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    const repository =
      options.repositoryPath === null || isAbsolute(options.repositoryPath) ? (options.repositoryPath ?? cwd) : join(cwd, options.repositoryPath);
    let target = repository;
    const result = await options.strategy.open(this.#git, cwd, repository, options, (path) => {
      target = path;
      return body(path);
    });
    // Reached only when the strategy exited successfully.
    for (const hook of options.hooksAt(LoopHookPoint.RUN_FINISHED)) {
      await this.#git.runHook(hook, repository, repository, target);
    }
    return result;
  }
}
