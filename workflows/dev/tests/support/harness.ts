/** Dev-owned fakes and harness for tests at the Workflow's injected seams. */
import { mkdirSync, mkdtempSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { DevDeps } from '../../backlog.ts';
import type { GitService } from '../../checkout.ts';
import type { Repository, TicketResult } from '../../records.ts';
import type { RepositoryWorkflow } from '../../repositories.ts';

import {
  AgentBuilder,
  AgentContext,
  type AgentProfile,
  type AgentRequest,
  CliOutcome,
  type CliRunner,
  DockerRuntime,
  type GitOptions,
  type GitService as LoopGitService,
  LoopHookError,
  LoopHookPoint,
  NativeHandle,
  ProcessFailedError,
} from '../../../../src/loop/index.ts';
import { implementTicket, loadPrompt } from '../../agent.ts';
import { processSpecs } from '../../backlog.ts';
import { devWorkflow } from '../../definition.ts';
import { FileExecutionStore } from '../../journal.ts';
import { type GithubFactory, GitHubRepo, TicketsTracker } from '../../platforms.ts';
import { AgentCommitSubject, Commit, RepositoryId } from '../../records.ts';
import { configuredRepositories } from '../../repositories.ts';
import { FakeGh, issueNode } from './fake-gh.ts';

export type Handler = (prompt: string) => string;

export const COMPLETED = { commit: 'abc', summary: 'done', verification: 'ran tests' };

export interface HarnessOptions {
  prs?: Record<string, unknown>[];
  specs?: Record<string, unknown>[];
  tickets?: Map<number, Record<string, unknown>[]>;
}

export interface RunOptions {
  /** Configures every phase (planner, verifier, cleanup) rather than only the implementation agent. */
  allPhases?: boolean;
  githubFactory?: GithubFactory;
  maxTicketFailures?: number;
  repositories?: readonly RepositoryWorkflow[];
  specNumber?: null | number;
  store?: FileExecutionStore;
}

/** A `CliRunner` that answers every run with the handler of its stage; a handler may throw `ProcessFailedError`. */
export class FakeRunner implements CliRunner {
  readonly chorePrompts: string[] = [];
  readonly planPrompts: string[] = [];
  readonly profiles: AgentProfile[] = [];
  readonly prompts: string[] = [];
  readonly verifyPrompts: string[] = [];
  choreHandler: Handler = () => envelope('', 'completed', { summary: 'No cleanup needed' });
  handler: Handler = () => '';
  planHandler: Handler = () => envelope('', 'failed', { reason: 'needs a product decision' });
  run(profile: AgentProfile, request: AgentRequest): Promise<CliOutcome> {
    this.profiles.push(profile);
    const stages: Record<string, { handler: () => Handler; prompts: string[] }> = {
      chore: { handler: () => this.choreHandler, prompts: this.chorePrompts },
      planner: { handler: () => this.planHandler, prompts: this.planPrompts },
      verifier: { handler: () => this.verifyHandler, prompts: this.verifyPrompts },
    };
    const stage = stages[profile.name ?? ''] ?? { handler: () => this.handler, prompts: this.prompts };
    stage.prompts.push(request.prompt);
    try {
      const output = stage.handler()(request.prompt);
      return Promise.resolve(new CliOutcome(output, new NativeHandle(profile.cli.name, `session${String(this.prompts.length)}`), 0));
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new Error(String(error)));
    }
  }

  verifyHandler: Handler = () => envelope('', 'completed', { verdict: 'inconclusive' });
}

/** An in-memory `GitService` over one branch: a commit list, how much of it origin already holds, a dirty flag. */
export class FakeWorkflowGit implements GitService {
  readonly commits: Commit[] = [new Commit('base0000', 'base')];
  dirty = false;
  readonly fetched: string[] = [];
  pushedCount = 1;
  readonly pushes: [string, string][] = [];
  remoteBranches = new Set(['main']);
  readonly restores: string[] = [];
  readonly #changed: string[] = [];

  /** Makes a commit the way an agent would, without a path or a message body, and resolves to its SHA. */
  agentCommit(subject: string): string {
    const sha = `c${String(this.commits.length).padStart(7, '0')}`;
    this.commits.push(new Commit(sha, subject));
    this.dirty = false;
    this.#changed.length = 0;
    return sha;
  }

  branchCommits(): Promise<Commit[]> {
    return Promise.resolve(this.commits.slice(1));
  }

  changedFiles(): Promise<string[]> {
    return Promise.resolve(this.#files());
  }

  commit(_path: string, subject: string, body = ''): Promise<Commit> {
    const commit = new Commit(`c${String(this.commits.length).padStart(7, '0')}`, subject, body);
    this.commits.push(commit);
    this.dirty = false;
    this.#changed.length = 0;
    return Promise.resolve(commit);
  }

  commitsSince(_path: string, sha: string): Promise<Commit[]> {
    return Promise.resolve(this.commits.slice(this.#index(sha) + 1));
  }

  /** Leaves an uncommitted change in the working tree. */
  edit(path = 'src/Basket.cs'): void {
    this.dirty = true;
    this.#changed.push(path);
  }

  fetch(path: string): Promise<void> {
    this.fetched.push(path);
    return Promise.resolve();
  }

  head(): Promise<Commit> {
    return Promise.resolve(this.#last());
  }

  initiativeCommits(_path: string, _base: string, prefix: string): Promise<Commit[]> {
    return Promise.resolve(this.commits.slice(1).filter((commit) => commit.subject.startsWith(prefix)));
  }

  isClean(): Promise<boolean> {
    return Promise.resolve(!this.dirty);
  }

  pushIfAhead(path: string, branch: string): Promise<boolean> {
    if (this.commits.length <= this.pushedCount) {
      return Promise.resolve(false);
    }
    this.pushes.push([path, branch]);
    this.pushedCount = this.commits.length;
    return Promise.resolve(true);
  }

  refSha(): Promise<string> {
    return Promise.resolve(this.commits[this.pushedCount - 1]?.sha ?? '');
  }

  remoteBranchExists(_path: string, branch: string): Promise<boolean> {
    return Promise.resolve(this.remoteBranches.has(branch));
  }

  restore(_path: string, sha: string): Promise<void> {
    this.commits.splice(this.#index(sha) + 1);
    this.dirty = false;
    this.#changed.length = 0;
    this.restores.push(sha);
    return Promise.resolve();
  }

  status(): Promise<string> {
    return Promise.resolve(this.#files().map((file) => ` M ${file}`).join('\n'));
  }

  #files(): string[] {
    return this.#changed.length > 0 ? [...this.#changed] : this.dirty ? ['src/Basket.cs'] : [];
  }

  #index(sha: string): number {
    const index = this.commits.findIndex((commit) => commit.sha === sha);
    if (index < 0) {
      throw new RangeError(`unknown commit ${sha}`);
    }
    return index;
  }

  #last(): Commit {
    const [last] = this.commits.slice(-1);
    if (last === undefined) {
      throw new RangeError('no commits');
    }
    return last;
  }
}

/** Wires the dev delivery to fakes: a fake CLI runner, git service, workflow git and `gh`, and a real execution store. */
export class DevHarness {
  readonly gh: FakeGh;
  readonly git = new FakeWorkflowGit();
  readonly github: GitHubRepo;
  readonly gitService: DirectoryWorktree;
  readonly logDir: string;
  /** The high-level phase records the Workflow reported, in order. */
  readonly records: string[] = [];
  readonly repository: Repository;
  readonly root: string;
  readonly runner = new FakeRunner();
  store: FileExecutionStore;
  readonly worktree: string;

  get prompts(): string[] {
    return this.runner.prompts;
  }

  constructor(directory: string, options: HarnessOptions = {}) {
    this.root = join(directory, 'harness');
    this.logDir = join(directory, 'logs');
    this.worktree = join(directory, 'worktree');
    this.gitService = new DirectoryWorktree(this.worktree);
    this.gh = new FakeGh({
      prs: options.prs,
      specs: options.specs ?? [
        issue(1, 'Checkout: Add login page', { labels: ['spec', 'repo:target:owner/repo', 'repo:base:main'] }),
      ],
      tickets: options.tickets ?? new Map([[1, [issue(10, 'Add login form')]]]),
    });
    this.github = new GitHubRepo('owner', 'repo', this.gh.run);
    this.store = new FileExecutionStore(this.logDir);
    this.repository = {
      checkout: this.root,
      ownerName: new RepositoryId('owner', 'repo'),
      worktrees: join(this.root, 'workspace', 'harness.worktrees'),
    };
  }

  /** The `gh` calls starting with `prefix`. */
  calls(...prefix: string[]): (readonly string[])[] {
    return this.gh.calls.filter((call) => prefix.every((part, index) => call[index] === part));
  }

  /** Commits the way an agent does and resolves to its completed response. */
  commitAndReport(number: number, options: { initiative?: string; subject?: string; summary?: string } = {}): string {
    const identifier = new AgentCommitSubject(options.initiative ?? 'Checkout', number);
    const sha = this.git.agentCommit(options.subject ?? `${identifier.toSubject()}work`);
    return envelope(identifier.toString(), 'completed', { ...COMPLETED, commit: sha, summary: options.summary ?? 'done' });
  }

  /** Commits, then answers with what `response` makes of the new SHA. */
  commitThen(response: (sha: string) => string): Handler {
    return () => response(this.git.agentCommit(`${new AgentCommitSubject('Checkout', 10).toSubject()}work`));
  }

  /** The default implementation delegate, or every configured phase except review, which review tests cover. */
  repositories(allPhases: boolean): readonly RepositoryWorkflow[] {
    if (allPhases) {
      return configuredRepositories(this.repository).map((workflow) => ({ ...workflow, runReview: undefined }));
    }
    const template = loadPrompt('implement');
    return [
      {
        repository: this.repository,
        runAgents: (context, workspace): Promise<TicketResult> => implementTicket(context, workspace, template),
      },
    ];
  }

  /** Runs the Workflow and resolves to the exit code. */
  async run(handler?: Handler, options: RunOptions = {}): Promise<number> {
    if (handler !== undefined) {
      this.runner.handler = handler;
    }
    if (options.store !== undefined) {
      this.store = options.store;
    }
    const factory: GithubFactory = options.githubFactory ?? (() => this.github);
    const workflow = devWorkflow({
      logger: {
        info: (message) => {
          this.records.push(message);
        },
        warn: (message) => {
          this.records.push(message);
        },
      },
    });
    return workflow.run(async (phases) => {
      const trackerGithub = factory(this.root);
      const deps: DevDeps = {
        git: this.git,
        githubFactory: factory,
        logDir: this.logDir,
        loopHooks: [],
        maxTicketFailures: options.maxTicketFailures ?? 2,
        newAgent: newAgentFor(this.runner, this.gitService, this.root),
        phases,
        specNumber: options.specNumber ?? null,
        store: this.store,
        ticketRepository: new RepositoryId('owner', 'repo'),
        tracker: new TicketsTracker('owner', 'repo', (args) => trackerGithub.run(args)),
      };
      return processSpecs(options.repositories ?? this.repositories(options.allPhases === true), deps);
    });
  }

  /** The `gh` calls that write issues or pull requests. */
  writes(): (readonly string[])[] {
    return this.gh.calls.filter((call) => call[0] === 'issue' || call[0] === 'pr');
  }
}

/** A worktree that is a plain directory; records the options it was opened with. */
export class DirectoryWorktree implements LoopGitService {
  closed = 0;
  readonly opened: GitOptions[] = [];
  openError: Error | null = null;
  constructor(readonly path: string) {}

  onOpen: () => void = () => undefined;

  async open<T>(_cwd: string, options: GitOptions, body: (path: string) => Promise<T>): Promise<T> {
    this.opened.push(options);
    this.onOpen();
    if (this.openError !== null) {
      throw this.openError;
    }
    mkdirSync(this.path, { recursive: true });
    try {
      return await body(this.path);
    } finally {
      this.closed += 1;
    }
  }
}

/** Returns the target repository's URL when a draft PR is created. */
export class FakeRepositoryGh {
  get calls(): (readonly string[])[] {
    return this.#gh.calls;
  }
  readonly #gh = new FakeGh({ prs: [], specs: [], tickets: new Map() });

  constructor(readonly repository: RepositoryId) {}

  readonly run = async (args: readonly string[]): Promise<string> => {
    const output = await this.#gh.run(args);
    return args[0] === 'pr' && args[1] === 'create' ? `https://github.com/${this.repository.toString()}/pull/99` : output;
  };
}

/** Routes Git calls to isolated fake histories and rejects unexpected checkout or worktree paths. */
export class FakeRepositoryGit implements GitService {
  constructor(readonly paths: ReadonlyMap<string, FakeWorkflowGit>) {}

  branchCommits(path: string): Promise<Commit[]> {
    return this.#at(path).branchCommits();
  }

  changedFiles(path: string): Promise<string[]> {
    return this.#at(path).changedFiles();
  }

  commit(path: string, subject: string, body?: string): Promise<Commit> {
    return this.#at(path).commit(path, subject, body);
  }

  commitsSince(path: string, sha: string): Promise<Commit[]> {
    return this.#at(path).commitsSince(path, sha);
  }

  fetch(path: string): Promise<void> {
    return this.#at(path).fetch(path);
  }

  head(path: string): Promise<Commit> {
    return this.#at(path).head();
  }

  initiativeCommits(path: string, base: string, prefix: string): Promise<Commit[]> {
    return this.#at(path).initiativeCommits(path, base, prefix);
  }

  isClean(path: string): Promise<boolean> {
    return this.#at(path).isClean();
  }

  pushIfAhead(path: string, branch: string): Promise<boolean> {
    return this.#at(path).pushIfAhead(path, branch);
  }

  refSha(path: string): Promise<string> {
    return this.#at(path).refSha();
  }

  remoteBranchExists(path: string, branch: string): Promise<boolean> {
    return this.#at(path).remoteBranchExists(path, branch);
  }

  restore(path: string, sha: string): Promise<void> {
    return this.#at(path).restore(path, sha);
  }

  status(path: string): Promise<string> {
    return this.#at(path).status();
  }

  #at(path: string): FakeWorkflowGit {
    const git = this.paths.get(path);
    if (git === undefined) {
      throw new RangeError(`unexpected path ${path}`);
    }
    return git;
  }
}

export function crash(output = ''): Handler {
  return () => {
    throw new ProcessFailedError({ argv: ['copilot'], exitCode: 1, signal: null, stderr: 'crashed', stdout: output });
  };
}

/** A JSON response envelope the way the agents write it. */
export function envelope(identifier = 'Checkout|10', status = 'completed', result: object = {}): string {
  return JSON.stringify({ identifier, result, status });
}

export function hookError(): LoopHookError {
  return new LoopHookError(LoopHookPoint.WORKTREE_READY, 'setup.sh', 'exit 1');
}

/** An issue node the way GraphQL returns it, without comments. */
export function issue(number: number, title: string, options: { labels?: readonly string[]; state?: string } = {}): Record<string, unknown> {
  return { ...issueNode(number, title, options.labels ?? [], options.state ?? 'OPEN'), comments: { nodes: [] } };
}

export function newAgentFor(runner: CliRunner, gitService: LoopGitService, cwd: string): () => AgentBuilder {
  return () => new AgentBuilder(runner, gitService, new DockerRuntime(), new AgentContext({ cwd }));
}

/** A directory for one test, resolved through symlinks. */
export function testDirectory(prefix: string): string {
  return realpathSync(mkdtempSync(join(tmpdir(), prefix)));
}
