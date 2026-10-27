import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  BranchStrategy,
  GitCli,
  GitOptions,
  GitRuntime,
  LoggingGitCli,
  type LoopHook,
  LoopHookError,
  MergeToHeadStrategy,
  notesOf,
  RunFinishedLoopHook,
  WorktreeError,
  WorktreeReadyLoopHook,
  WorktreeRemovingLoopHook,
} from '../../src/loop/index.ts';
import { ProcessFailedError } from '../../src/loop/run/process.ts';
import { rejectionOf } from './support/doubles.ts';

let root = '';
let repository = '';

const git = (path: string, ...args: string[]): string => {
  const result = Bun.spawnSync(['git', '-C', path, ...args], { stderr: 'pipe', stdout: 'pipe' });
  if (result.exitCode !== 0) {
    throw new Error(`git ${args.join(' ')} failed: ${result.stderr.toString()}`);
  }
  return result.stdout.toString().trim();
};

const commit = (path: string, value = 'initial'): string => {
  writeFileSync(join(path, 'cart.txt'), value);
  git(path, 'add', '-A');
  git(path, 'commit', '-m', value);
  return git(path, 'rev-parse', 'HEAD');
};

const runtime = (cli: GitCli = new GitCli()): GitRuntime => new GitRuntime(cli);

const options = (branch = 'review'): GitOptions =>
  new GitOptions({ repositoryPath: repository, rootPath: join(root, 'trees'), strategy: new BranchStrategy(branch) });

const mergeOptions = (...hooks: LoopHook[]): GitOptions =>
  new GitOptions({ loopHooks: hooks, rootPath: join(root, 'trees'), strategy: new MergeToHeadStrategy() });

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'loop-worktree-'));
  repository = join(root, 'repo');
  mkdirSync(repository);
  git(repository, 'init');
  git(repository, 'config', 'user.name', 'Test');
  git(repository, 'config', 'user.email', 'test@example.com');
  git(repository, 'config', 'commit.gpgsign', 'false');
  commit(repository);
  git(repository, 'branch', '-M', 'host');
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

describe('a worktree scope over real git', () => {
  test('a successful dirty run never auto-commits and keeps the worktree', async () => {
    const initial = git(repository, 'rev-parse', 'HEAD');
    let path = '';

    const rejected = await rejectionOf(
      runtime().open(repository, options(), (opened) => {
        path = opened;
        writeFileSync(join(path, 'cart.txt'), 'unverified');
        return Promise.resolve();
      }),
    );

    expect(rejected).toBeInstanceOf(WorktreeError);
    expect((rejected as Error).message).toContain('uncommitted');
    expect(existsSync(path)).toBe(true);
    expect(git(path, 'rev-parse', 'HEAD')).toBe(initial);
    expect(readFileSync(join(path, 'cart.txt'), 'utf8')).toBe('unverified');
  });

  test('a failed clean run removes the directory but retains commits, and rethrows the original', async () => {
    const failure = new Error('push rejected');
    let path = '';
    let sha = '';

    const rejected = await rejectionOf(
      runtime().open(repository, options(), (opened) => {
        path = opened;
        sha = commit(path, 'verified');
        return Promise.reject(failure);
      }),
    );

    expect(rejected).toBe(failure);
    expect(existsSync(path)).toBe(false);
    expect(git(repository, 'rev-parse', 'review')).toBe(sha);
    await runtime().open(repository, options(), (recovered) => {
      expect(git(recovered, 'rev-parse', 'HEAD')).toBe(sha);
      return Promise.resolve();
    });
  });

  test('a failed dirty run is kept, reused without losing the original error, then removed once committed', async () => {
    const failure = new Error('tests failed');
    let path = '';

    const rejected = await rejectionOf(
      runtime().open(repository, options(), (opened) => {
        path = opened;
        writeFileSync(join(path, 'cart.txt'), 'unfinished');
        writeFileSync(join(path, 'new.txt'), 'untracked');
        return Promise.reject(failure);
      }),
    );

    expect(rejected).toBe(failure);
    expect(existsSync(path)).toBe(true);
    await runtime().open(repository, options(), (recovered) => {
      expect(recovered).toBe(path);
      expect(readFileSync(join(path, 'new.txt'), 'utf8')).toBe('untracked');
      commit(path, 'verified repair');
      return Promise.resolve();
    });
    expect(existsSync(path)).toBe(false);
  });

  test('a worktree root reached through a symlink is reused', async () => {
    mkdirSync(join(root, 'real'));
    symlinkSync(join(root, 'real'), join(root, 'link'));
    const linked = new GitOptions({
      repositoryPath: repository,
      rootPath: join(root, 'link', 'trees'),
      strategy: new BranchStrategy('review'),
    });
    await rejectionOf(
      runtime().open(repository, linked, (path) => {
        writeFileSync(join(path, 'cart.txt'), 'unfinished');
        return Promise.reject(new Error('interrupted'));
      }),
    );

    await runtime().open(repository, linked, (path) => {
      expect(readFileSync(join(path, 'cart.txt'), 'utf8')).toBe('unfinished');
      commit(path, 'done');
      return Promise.resolve();
    });
  });

  test('a cleanup failure does not mask the run failure and is attached to its notes', async () => {
    class FailedRemoval extends GitCli {
      override removeWorktree(): Promise<void> {
        return Promise.reject(new Error('cleanup unavailable'));
      }
    }
    const failure = new Error('original failure');

    const rejected = await rejectionOf(runtime(new FailedRemoval()).open(repository, options(), () => Promise.reject(failure)));

    expect(rejected).toBe(failure);
    expect(notesOf(failure).join('\n')).toContain('cleanup unavailable');
  });

  test('a successful merge-to-head run fast-forwards HEAD, deletes the temporary branch and removes the worktree', async () => {
    let path = '';
    let branch = '';
    let sha = '';

    await runtime().open(repository, mergeOptions(), (opened) => {
      path = opened;
      branch = git(path, 'branch', '--show-current');
      sha = commit(path, 'verified');
      return Promise.resolve();
    });

    expect(git(repository, 'rev-parse', 'HEAD')).toBe(sha);
    expect(git(repository, 'branch', '--format=%(refname:short)').split('\n')).not.toContain(branch);
    expect(existsSync(path)).toBe(false);
  });

  test('a divergent host keeps the temporary branch for recovery and raises the merge failure', async () => {
    let path = '';
    let branch = '';
    let sha = '';

    const rejected = await rejectionOf(
      runtime().open(repository, mergeOptions(), (opened) => {
        path = opened;
        branch = git(path, 'branch', '--show-current');
        sha = commit(path, 'worktree change');
        commit(repository, 'host change');
        return Promise.resolve();
      }),
    );

    expect(rejected).toBeInstanceOf(ProcessFailedError);
    expect(git(repository, 'rev-parse', branch)).toBe(sha);
    expect(existsSync(path)).toBe(false);
    expect(readFileSync(join(repository, 'cart.txt'), 'utf8')).toBe('host change');
  });

  test('stale metadata and an empty orphan directory are repaired', async () => {
    const config = options();
    let path = '';
    await rejectionOf(
      runtime().open(repository, config, (opened) => {
        path = opened;
        writeFileSync(join(path, 'cart.txt'), 'unfinished');
        return Promise.reject(new Error('interrupted'));
      }),
    );
    rmSync(path, { force: true, recursive: true });
    mkdirSync(path, { recursive: true });

    await runtime().open(repository, config, (reopened) => {
      expect(git(reopened, 'branch', '--show-current')).toBe('review');
      return Promise.resolve();
    });

    expect(existsSync(path)).toBe(false);
  });

  test('a nonempty orphan directory is never deleted', async () => {
    const target = join(root, 'trees', 'review');
    mkdirSync(target, { recursive: true });
    writeFileSync(join(target, 'valuable.txt'), 'keep');

    const rejected = await rejectionOf(runtime().open(repository, options(), () => Promise.resolve()));

    expect((rejected as Error).message).toContain('orphan');
    expect(readFileSync(join(target, 'valuable.txt'), 'utf8')).toBe('keep');
  });

  test('an exact base wins over the remote branch when creating the local branch', async () => {
    const base = git(repository, 'rev-parse', 'HEAD');
    const later = commit(repository, 'later');
    git(repository, 'update-ref', 'refs/remotes/origin/review', later);
    const config = new GitOptions({ rootPath: join(root, 'trees'), strategy: new BranchStrategy('review', base) });

    await runtime().open(repository, config, (path) => {
      expect(git(path, 'rev-parse', 'HEAD')).toBe(base);
      return Promise.resolve();
    });
  });

  test('an existing worktree on the wrong branch is rejected and kept', async () => {
    const target = join(root, 'trees', 'review');
    mkdirSync(join(root, 'trees'));
    git(repository, 'worktree', 'add', '-b', 'other', target);

    const rejected = await rejectionOf(runtime().open(repository, options(), () => Promise.resolve()));

    expect(rejected).toBeInstanceOf(WorktreeError);
    expect((rejected as Error).message).toContain('branch');
    expect(existsSync(target)).toBe(true);
  });

  test('a setup hook that wrote files then failed keeps them and the hook error', async () => {
    class FailedSetup extends GitCli {
      override runHook(hook: LoopHook, cwd: string): Promise<void> {
        writeFileSync(join(cwd, 'setup.txt'), 'unfinished setup');
        return Promise.reject(new LoopHookError(hook.point, hook.command, 'failed'));
      }
    }
    const config = new GitOptions({
      loopHooks: [new WorktreeReadyLoopHook('setup')],
      rootPath: join(root, 'trees'),
      strategy: new BranchStrategy('setup'),
    });

    const rejected = await rejectionOf(
      runtime(new FailedSetup()).open(repository, config, () => Promise.reject(new Error('agent should not start'))),
    );

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect(readFileSync(join(root, 'trees', 'setup', 'setup.txt'), 'utf8')).toBe('unfinished setup');
  });

  test('an orphan holding only a stale owned git link is recreated', async () => {
    const target = join(root, 'trees', 'review');
    mkdirSync(target, { recursive: true });
    writeFileSync(join(target, '.git'), `gitdir: ${join(repository, '.git', 'worktrees', 'missing')}\n`);

    await runtime().open(repository, options(), (path) => {
      expect(git(path, 'branch', '--show-current')).toBe('review');
      return Promise.resolve();
    });

    expect(existsSync(target)).toBe(false);
  });

  test('a dry run does not remove an empty orphan directory', async () => {
    const target = join(root, 'trees', 'review');
    mkdirSync(target, { recursive: true });

    await runtime(new LoggingGitCli(() => undefined)).open(repository, options(), () => Promise.resolve());

    expect(existsSync(target)).toBe(true);
  });

  test('an administratively locked worktree is not reused', async () => {
    let path = '';
    await rejectionOf(
      runtime().open(repository, options(), (opened) => {
        path = opened;
        writeFileSync(join(path, 'cart.txt'), 'unfinished');
        return Promise.reject(new Error('interrupted'));
      }),
    );
    git(repository, 'worktree', 'lock', path);

    const rejected = await rejectionOf(runtime().open(repository, options(), () => Promise.reject(new Error('locked worktree reused'))));

    expect((rejected as Error).message).toContain('locked');
    expect(readFileSync(join(path, 'cart.txt'), 'utf8')).toBe('unfinished');
  });

  test('the host checkout itself is never managed as a worktree', async () => {
    const config = new GitOptions({ rootPath: root, strategy: new BranchStrategy('repo') });

    const rejected = await rejectionOf(runtime().open(repository, config, () => Promise.resolve()));

    expect(rejected).toBeInstanceOf(WorktreeError);
    expect(git(repository, 'rev-parse', '--show-toplevel')).toBe(repository);
  });
});

describe('Loop hooks over real shell commands and git', () => {
  test('worktree-ready, worktree-removing and run-finished run in their directories with the LOOP variables', async () => {
    const log = join(root, 'hooks.log');
    const record = (point: string): string => `echo "${point} $(pwd) $LOOP_HOOK_POINT $LOOP_REPOSITORY $LOOP_WORKTREE" >> '${log}'`;
    const config = mergeOptions(
      new WorktreeReadyLoopHook(record('ready')),
      new WorktreeRemovingLoopHook(record('removing')),
      new RunFinishedLoopHook(record('finished')),
    );
    let path = '';

    await runtime().open(repository, config, (opened) => {
      path = opened;
      commit(path, 'verified');
      return Promise.resolve();
    });

    const [ready, removing, finished] = readFileSync(log, 'utf8').trim().split('\n');
    expect(ready).toBe(`ready ${path} worktree-ready ${repository} ${path}`);
    expect(removing).toBe(`removing ${path} worktree-removing ${repository} ${path}`);
    expect(finished).toBe(`finished ${repository} run-finished ${repository} ${path}`);
  });

  test('a ready hook may prepare ignored files and leaves the worktree clean', async () => {
    writeFileSync(join(repository, '.gitignore'), '.env\n');
    commit(repository, 'ignore env');
    const config = mergeOptions(new WorktreeReadyLoopHook('echo SECRET=1 > .env'));
    let path = '';

    await runtime().open(repository, config, (opened) => {
      path = opened;
      expect(readFileSync(join(path, '.env'), 'utf8')).toBe('SECRET=1\n');
      return Promise.resolve();
    });

    expect(existsSync(path)).toBe(false);
  });

  test('a real failing hook reports its output and removes the clean worktree', async () => {
    const config = mergeOptions(new WorktreeReadyLoopHook('echo oops; echo bad >&2; exit 4'));

    const rejected = await rejectionOf(runtime().open(repository, config, () => Promise.reject(new Error('agent should not start'))));

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect(rejected).toMatchObject({ output: 'oops\nbad\n', point: 'worktree-ready' });
    expect(existsSync(join(root, 'trees'))).toBe(true);
    expect(git(repository, 'worktree', 'list')).not.toContain('tmp_');
  });

  test('a real hook that exceeds its timeout raises a LoopHookError', async () => {
    const config = mergeOptions(new WorktreeReadyLoopHook('sleep 30', 0.2));

    const rejected = await rejectionOf(runtime().open(repository, config, () => Promise.resolve()));

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect((rejected as Error).message).toContain('timed out after 0.2s');
  });

  test('a removing hook that dirties the worktree keeps it and raises', async () => {
    const config = mergeOptions(new WorktreeRemovingLoopHook('echo late > late.txt'));
    let path = '';

    const rejected = await rejectionOf(
      runtime().open(repository, config, (opened) => {
        path = opened;
        return Promise.resolve();
      }),
    );

    expect(rejected).toBeInstanceOf(WorktreeError);
    expect(existsSync(join(path, 'late.txt'))).toBe(true);
  });
});
