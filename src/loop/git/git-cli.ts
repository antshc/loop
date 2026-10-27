import { lstat, readdir, readFile, rmdir, unlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';

import type { LoopHook } from '../hooks/loop.ts';

import { getLogger } from '../diagnostics.ts';
import { LoopHookError } from '../hooks/loop.ts';
import { ProcessTimeoutError } from '../run/process.ts';
import { type ProcessResult, type ProcessRunner, processRunner } from '../run/process.ts';
import { WorktreeError } from './errors.ts';
import { pathExists, resolvePath } from './paths.ts';

const logger = getLogger('loop.git.git_cli');

/** Runs git against a given repository; the process seam is injectable for testing. */
export class GitCli {
  readonly #processes: ProcessRunner;

  constructor(processes: ProcessRunner = processRunner) {
    this.#processes = processes;
  }

  /** Create or validate a named worktree without resetting existing local commits. */
  async addWorktree(repository: string, target: string, branch: string, base: null | string = null): Promise<void> {
    await this.#git(repository, 'check-ref-format', '--branch', branch);
    if ((await pathExists(target)) && (await this.reuseWorktree(repository, target, branch))) {
      return;
    }
    await this.#git(repository, 'worktree', 'prune', '--expire', 'now');
    if (!(await this.#succeeds(repository, 'show-ref', '--verify', '--quiet', `refs/heads/${branch}`))) {
      const remote =
        base === null && (await this.#succeeds(repository, 'show-ref', '--verify', '--quiet', `refs/remotes/origin/${branch}`));
      await this.#git(repository, 'branch', branch, remote ? `origin/${branch}` : base === null || base === '' ? 'HEAD' : base);
    }
    logger.info(`adding worktree ${target} on branch ${branch}`);
    await this.#git(repository, 'worktree', 'add', target, branch);
  }

  async deleteBranch(repository: string, branch: string): Promise<void> {
    logger.info(`deleting branch ${branch}`);
    await this.#git(repository, 'branch', '-d', branch);
  }

  async fetch(repository: string): Promise<void> {
    logger.info(`fetching ${repository}`);
    await this.#git(repository, 'fetch', '--all', '--prune');
  }

  /** Inspect tracked and untracked edits solely to decide lifecycle cleanup. */
  async isClean(worktree: string): Promise<boolean> {
    return (await this.#output(worktree, 'status', '--porcelain', '--untracked-files=all')) === '';
  }

  async mergeFfOnly(repository: string, branch: string): Promise<void> {
    logger.info(`merging ${branch} fast-forward only`);
    await this.#git(repository, 'merge', '--ff-only', branch);
  }

  /** Detach the worktree at `target`; git refuses when it has uncommitted changes unless `force`. The branch is kept. */
  async removeWorktree(repository: string, target: string, options: { force?: boolean } = {}): Promise<void> {
    logger.info(`removing worktree ${target}`);
    await this.#git(repository, 'worktree', 'remove', ...(options.force === true ? ['--force'] : []), target);
  }

  /** Runs `hook` through the shell in `cwd`; a non-zero exit or a timeout raises LoopHookError. */
  async runHook(hook: LoopHook, cwd: string, repository: string, worktree: string): Promise<void> {
    logger.debug(`loop hook ${hook.point}: ${hook.command}`);
    let result: ProcessResult;
    try {
      result = await this.#processes.run(['/bin/sh', '-c', hook.command], {
        cwd,
        env: { LOOP_HOOK_POINT: hook.point, LOOP_REPOSITORY: repository, LOOP_WORKTREE: worktree },
        timeoutSec: hook.timeoutSec,
      });
    } catch (error) {
      if (error instanceof ProcessTimeoutError) {
        throw new LoopHookError(hook.point, hook.command, `timed out after ${String(hook.timeoutSec)}s`, { cause: error });
      }
      throw error;
    }
    if (result.exitCode !== 0) {
      throw new LoopHookError(hook.point, hook.command, result.stdout + result.stderr);
    }
  }

  protected async invoke(repository: string, args: readonly string[], check: boolean): Promise<ProcessResult> {
    const command = ['git', '-C', repository, ...args];
    logger.debug(`git: ${command.join(' ')}`);
    const result = await this.#processes.run(command, { check });
    if (result.exitCode !== 0) {
      logger.debug(`git exited ${String(result.exitCode)}: ${result.stderr.trim()}`);
    }
    return result;
  }

  /** Whether the worktree already at `target` is the one wanted; false when it was disposable and removed. */
  protected async reuseWorktree(repository: string, target: string, branch: string): Promise<boolean> {
    if ((await resolvePath(target)) === (await resolvePath(repository))) {
      throw new WorktreeError(`Cannot manage the host checkout as a linked worktree: ${target}`);
    }
    const info = await lstat(target);
    if (!info.isDirectory() || info.isSymbolicLink()) {
      throw new WorktreeError(`Invalid worktree path: ${target}`);
    }
    const entries = await readdir(target, { withFileTypes: true });
    if (entries.length === 0) {
      await rmdir(target);
      return false;
    }
    const probe = await this.invoke(target, ['rev-parse', '--show-toplevel'], false);
    if (probe.exitCode !== 0) {
      if (await this.#removeBrokenLink(repository, target, entries)) {
        return false;
      }
      throw new WorktreeError(`Nonempty orphan worktree preserved for recovery: ${target}`);
    }
    if ((await resolvePath(probe.stdout.trim())) !== (await resolvePath(target))) {
      throw new WorktreeError(`Unexpected worktree root: ${target}`);
    }
    const common = ['rev-parse', '--path-format=absolute', '--git-common-dir'];
    if ((await this.#output(target, ...common)) !== (await this.#output(repository, ...common))) {
      throw new WorktreeError(`Worktree belongs to another repository: ${target}`);
    }
    const resolved = await resolvePath(target);
    const records = (await this.#output(repository, 'worktree', 'list', '--porcelain', '-z')).split('\0\0');
    const registered = records.map((record) => record.split('\0')).find((fields) => fields.includes(`worktree ${resolved}`));
    if (registered === undefined || registered.some((field) => field.startsWith('locked'))) {
      throw new WorktreeError(`Worktree is unregistered or administratively locked: ${target}`);
    }
    const actual = await this.invoke(target, ['symbolic-ref', '--quiet', '--short', 'HEAD'], false);
    if (actual.exitCode !== 0 || actual.stdout.trim() !== branch) {
      throw new WorktreeError(`Worktree branch does not match ${branch}: ${target}`);
    }
    logger.info(`reusing worktree ${target} on branch ${branch}`);
    return true;
  }

  async #git(repository: string, ...args: string[]): Promise<void> {
    await this.invoke(repository, args, true);
  }

  async #output(repository: string, ...args: string[]): Promise<string> {
    return (await this.invoke(repository, args, true)).stdout.trim();
  }

  /** Only an empty directory or a broken link without user files is disposable. */
  async #removeBrokenLink(repository: string, target: string, entries: readonly { isFile: () => boolean; name: string }[]): Promise<boolean> {
    const [entry] = entries;
    if (entries.length !== 1 || entry?.name !== '.git' || !entry.isFile()) {
      return false;
    }
    const text = (await readFile(join(target, '.git'), 'utf8')).trim();
    if (!text.startsWith('gitdir: ')) {
      return false;
    }
    const common = await this.#output(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir');
    const link = await resolvePath(resolve(target, text.slice(8)));
    if (dirname(link) !== join(await resolvePath(common), 'worktrees') || (await pathExists(link))) {
      return false;
    }
    await unlink(join(target, '.git'));
    await rmdir(target);
    return true;
  }

  async #succeeds(repository: string, ...args: string[]): Promise<boolean> {
    return (await this.invoke(repository, args, false)).exitCode === 0;
  }
}
