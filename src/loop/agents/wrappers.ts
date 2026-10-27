import type { DockerService } from '../docker/runtime.ts';
import type { GitService } from '../git/service.ts';
import type { AgentRequest, AgentResult } from '../run/request.ts';
import type { AgentClient } from './client.ts';

import { GitOptions } from '../git/service.ts';
import { AgentContext } from '../run/context.ts';

/** Composes an AgentClient and delegates common lifecycle operations. */
export abstract class AgentWrapper implements AgentClient {
  protected readonly defaults: AgentContext;
  protected readonly inner: AgentClient;

  protected constructor(inner: AgentClient, defaults: AgentContext = new AgentContext()) {
    this.inner = inner;
    this.defaults = defaults;
  }

  close(): Promise<void> {
    return this.inner.close();
  }

  abstract run(request: AgentRequest, context?: AgentContext): Promise<AgentResult>;
}

export class DockerAgent extends AgentWrapper {
  readonly #docker: DockerService;

  constructor(inner: AgentClient, docker: DockerService, defaults?: AgentContext) {
    super(inner, defaults);
    this.#docker = docker;
  }

  async run(request: AgentRequest, context?: AgentContext): Promise<AgentResult> {
    const configured = await this.#docker.configure(context ?? this.defaults);
    return this.inner.run(request, configured);
  }
}

export class GitAgent extends AgentWrapper {
  readonly #git: GitService;
  readonly #options: GitOptions;

  constructor(inner: AgentClient, git: GitService, options?: GitOptions | null, defaults?: AgentContext) {
    super(inner, defaults);
    this.#git = git;
    this.#options = options ?? new GitOptions();
  }

  run(request: AgentRequest, context?: AgentContext): Promise<AgentResult> {
    const original = context ?? this.defaults;
    // The strategy's git lifecycle surrounds the entire delegated execution.
    // The worktree is inside cwd, so the CLI already has access to it.
    return this.#git.open(original.cwd, this.#options, (workdir) => this.inner.run(request, original.with({ cwd: workdir })));
  }
}
