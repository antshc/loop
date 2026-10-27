/** Git reads and publication for the dev Workflow. */
import { CommandError, runCommand } from './commands.ts';
import { Commit } from './records.ts';

/** Git operations on a given checkout or worktree. */
export interface GitService {
  /** Commits after `origin/<base>` up to HEAD, oldest first, with their bodies. */
  branchCommits: (path: string, base: string) => Promise<Commit[]>;
  /** Paths with uncommitted changes (renames report the new path), untracked files included. */
  changedFiles: (path: string) => Promise<string[]>;
  /** Stages every change in `path`, commits it and returns the new HEAD; used for verifier and cleanup edits. */
  commit: (path: string, subject: string, body?: string) => Promise<Commit>;
  /** Commits after `sha` up to HEAD, oldest first. */
  commitsSince: (path: string, sha: string) => Promise<Commit[]>;
  fetch: (path: string) => Promise<void>;
  head: (path: string) => Promise<Commit>;
  /** Commits after `origin/<base>` whose subject starts with `prefix`, oldest first. */
  initiativeCommits: (path: string, base: string, prefix: string) => Promise<Commit[]>;
  isClean: (path: string) => Promise<boolean>;
  /** Pushes `branch` when it holds commits beyond its `origin` counterpart (or beyond `base` when it has none); resolves to whether it pushed. */
  pushIfAhead: (path: string, branch: string, base: string) => Promise<boolean>;
  refSha: (path: string, ref: string) => Promise<string>;
  remoteBranchExists: (path: string, branch: string) => Promise<boolean>;
  /** Hard-resets `path` to `sha` and removes untracked files and directories. */
  restore: (path: string, sha: string) => Promise<void>;
  /** The raw porcelain status, for comparing a checkout before and after a read-only run. */
  status: (path: string) => Promise<string>;
}

/** Runs `command` in `cwd` and resolves to stdout; the injectable process seam. */
export type Run = (command: readonly string[], options: { cwd: string }) => Promise<string>;

const FIELD_SEP = '\u001F';
const RECORD_SEP = '\u001E';
const LOG_FORMAT = `%H${FIELD_SEP}%s${FIELD_SEP}%b${RECORD_SEP}`;

/** The real `GitService`: the `git` CLI; `run` is the injectable process seam. */
export class CliGitService implements GitService {
  readonly #run: Run;

  constructor(run: Run = (command, options) => runCommand(command, options)) {
    this.#run = run;
  }

  branchCommits(path: string, base: string): Promise<Commit[]> {
    return this.#log(path, '--reverse', `origin/${base}..HEAD`);
  }

  async changedFiles(path: string): Promise<string[]> {
    const files: string[] = [];
    for (const line of (await this.#git(path, 'status', '--porcelain', '--untracked-files=all')).split(/\r?\n/)) {
      const entry = (line.slice(3).split(' -> ').at(-1) ?? '').trim().replaceAll(/^"|"$/g, '');
      if (entry !== '') {
        files.push(entry);
      }
    }
    return files;
  }

  async commit(path: string, subject: string, body = ''): Promise<Commit> {
    await this.#git(path, 'add', '-A');
    await this.#git(path, 'commit', '-m', subject, ...(body.trim() === '' ? [] : ['-m', body]));
    return this.head(path);
  }

  commitsSince(path: string, sha: string): Promise<Commit[]> {
    return this.#log(path, '--reverse', `${sha}..HEAD`);
  }

  async fetch(path: string): Promise<void> {
    await this.#git(path, 'fetch', '--all', '--prune');
  }

  async head(path: string): Promise<Commit> {
    const [commit] = await this.#log(path, '-1');
    if (commit === undefined) {
      throw new CommandError(`git -C ${path} log -1`, null, 'the repository has no commits');
    }
    return commit;
  }

  async initiativeCommits(path: string, base: string, prefix: string): Promise<Commit[]> {
    const commits = await this.#log(path, '--reverse', '--fixed-strings', `--grep=${prefix}`, `origin/${base}..HEAD`);
    // `--grep` also matches body lines, so keep only subjects that carry the prefix.
    return commits.filter((commit) => commit.subject.startsWith(prefix));
  }

  async isClean(path: string): Promise<boolean> {
    return (await this.#git(path, 'status', '--porcelain')).trim() === '';
  }

  async pushIfAhead(path: string, branch: string, base: string): Promise<boolean> {
    if (!(await this.#succeeds(path, 'show-ref', '--verify', '--quiet', `refs/heads/${branch}`))) {
      return false;
    }
    const upstream = (await this.remoteBranchExists(path, branch)) ? `origin/${branch}` : `origin/${base}`;
    if ((await this.#git(path, 'rev-list', `${upstream}..${branch}`)).trim() === '') {
      return false;
    }
    await this.#git(path, 'push', 'origin', branch);
    return true;
  }

  async refSha(path: string, ref: string): Promise<string> {
    return (await this.#git(path, 'rev-parse', ref)).trim();
  }

  remoteBranchExists(path: string, branch: string): Promise<boolean> {
    return this.#succeeds(path, 'show-ref', '--verify', '--quiet', `refs/remotes/origin/${branch}`);
  }

  async restore(path: string, sha: string): Promise<void> {
    await this.#git(path, 'reset', '--hard', sha);
    await this.#git(path, 'clean', '-fd');
  }

  status(path: string): Promise<string> {
    return this.#git(path, 'status', '--porcelain', '--untracked-files=all');
  }

  #git(path: string, ...args: string[]): Promise<string> {
    return this.#run(['git', ...args], { cwd: path });
  }

  async #log(path: string, ...args: string[]): Promise<Commit[]> {
    const output = await this.#git(path, 'log', `--format=${LOG_FORMAT}`, ...args);
    return output
      .split(RECORD_SEP)
      .map((record) => record.replace(/^\n+/, ''))
      .filter((record) => record !== '')
      .map((record) => {
        const [sha = '', subject = '', ...rest] = record.split(FIELD_SEP);
        return new Commit(sha, subject, (rest.join(FIELD_SEP) || '').trim());
      });
  }

  async #succeeds(path: string, ...args: string[]): Promise<boolean> {
    try {
      await this.#git(path, ...args);
    } catch (error) {
      if (error instanceof CommandError) {
        return false;
      }
      throw error;
    }
    return true;
  }
}
