import { type ProcessRunner, processRunner } from '../../../src/loop/index.ts';
import { Blocked } from './state.ts';

const COMMAND_TIMEOUT_SEC = 120;

/** What the Workflow needs from Git outside Loop's worktree lifecycle; tests replace it with an in-memory double. */
export interface WorkflowGit {
  /** Fast-forward a known published recovery branch to the current PR head. */
  advance: (path: string, sha: string) => Promise<void>;
  /** Include untracked files in the publication gate. */
  clean: (path: string) => Promise<boolean>;
  /** Commit verified changes, returning the actual SHA. */
  commit: (path: string, message: string) => Promise<string>;
  /** Fetch an expected PR revision without changing the host checkout. */
  fetchRevision: (path: string, sha: string) => Promise<void>;
  /** Read the actual commit rather than trusting an agent report. */
  head: (path: string) => Promise<string>;
  /** Publish recovery-branch commits to the PR branch without force. */
  publish: (path: string, branch: string) => Promise<void>;
  /** Execute a bounded Git command without shell interpolation. */
  text: (path: string, ...args: string[]) => Promise<string>;
  /** Check checkout identity before changing refs or worktrees. */
  validate: (path: string, repo: string) => Promise<void>;
}

/** Own workflow Git inspection, explicit commits, and non-forced publication. */
export class Git implements WorkflowGit {
  readonly #runner: ProcessRunner;

  constructor(runner: ProcessRunner = processRunner) {
    this.#runner = runner;
  }

  async advance(path: string, sha: string): Promise<void> {
    await this.text(path, 'merge', '--ff-only', sha);
  }

  async clean(path: string): Promise<boolean> {
    return (await this.text(path, 'status', '--porcelain')) === '';
  }

  async commit(path: string, message: string): Promise<string> {
    await this.text(path, 'add', '-A');
    await this.text(path, 'commit', '-m', message);
    return this.head(path);
  }

  async fetchRevision(path: string, sha: string): Promise<void> {
    await this.text(path, 'fetch', 'origin', sha);
  }

  head(path: string): Promise<string> {
    return this.text(path, 'rev-parse', 'HEAD');
  }

  async publish(path: string, branch: string): Promise<void> {
    await this.text(path, 'check-ref-format', `refs/heads/${branch}`);
    await this.text(path, 'push', 'origin', `HEAD:refs/heads/${branch}`);
  }

  async text(path: string, ...args: string[]): Promise<string> {
    const result = await this.#runner.run(['git', '-C', path, ...args], { check: true, timeoutSec: COMMAND_TIMEOUT_SEC });
    return result.stdout.trim();
  }

  async validate(path: string, repo: string): Promise<void> {
    const remote = await this.text(path, 'remote', 'get-url', 'origin');
    const slug = remote.replace(/^(git@github\.com:|https:\/\/github\.com\/)/, '').replace(/\.git$/, '');
    if (slug.toLowerCase() !== repo.toLowerCase()) throw new Blocked(`Origin does not match ${repo}: ${path}`);
  }
}
