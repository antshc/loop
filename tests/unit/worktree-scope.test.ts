import { afterEach, beforeEach, describe, expect, test } from 'bun:test';

import { setLogSink } from '../../src/loop/diagnostics.ts';
import {
  AgentBuilder,
  AgentContext,
  AgentRequest,
  BranchStrategy,
  GitCli,
  GitOptions,
  GitRuntime,
  HeadStrategy,
  type LoopHook,
  LoopHookError,
  LoopHookPoint,
  MergeToHeadStrategy,
  notesOf,
  RunFinishedLoopHook,
  ValueError,
  WorktreeError,
  WorktreeReadyLoopHook,
  WorktreeRemovingLoopHook,
} from '../../src/loop/index.ts';
import { Cancelled } from '../../src/loop/run/cancelled.ts';
import { RecordingDocker, RecordingRunner, rejectionOf } from './support/doubles.ts';
import { type FakeGitOptions, FakeGitProcesses } from './support/fake-git.ts';

const REPO = '/repo';

const runtimeOver = (git: FakeGitProcesses): GitRuntime => new GitRuntime(new GitCli(git));

const worktreeOptions = (strategy: BranchStrategy | MergeToHeadStrategy, ...hooks: LoopHook[]): GitOptions =>
  new GitOptions({ loopHooks: hooks, rootPath: 'wt', strategy });

const hookOptions = (...commands: (LoopHook | string)[]): GitOptions =>
  worktreeOptions(
    new BranchStrategy('loop/a'),
    ...commands.map((command) => (typeof command === 'string' ? new WorktreeReadyLoopHook(command, 5) : command)),
  );

const fixture = (behavior: FakeGitOptions = { missing: ['refs/'] }): FakeGitProcesses => new FakeGitProcesses(behavior);

describe('the git runtime', () => {
  test('head makes no git calls and runs the body in the repository', async () => {
    const git = fixture({});

    const path = await runtimeOver(git).open(REPO, new GitOptions({ rootPath: 'wt' }), (target) => Promise.resolve(target));

    expect(path).toBe(REPO);
    expect(git.commands).toEqual([]);
  });

  test('head runs in the repository path', async () => {
    const options = new GitOptions({ repositoryPath: 'services/api', strategy: new HeadStrategy() });

    expect(await runtimeOver(fixture({})).open(REPO, options, (target) => Promise.resolve(target))).toBe('/repo/services/api');
  });

  test('head ignores a root outside cwd', async () => {
    await runtimeOver(fixture({})).open(REPO, new GitOptions({ rootPath: '../x' }), () => Promise.resolve());
  });

  test('merge-to-head merges, then deletes the temporary branch, after removing the worktree', async () => {
    const git = fixture();
    let target = '';

    await runtimeOver(git).open(REPO, new GitOptions({ rootPath: 'wt', strategy: new MergeToHeadStrategy() }), (path) => {
      target = path;
      return Promise.resolve();
    });

    expect(target).toMatch(/^\/repo\/wt\/tmp_[0-9a-f]{8}$/);
    const branch = target.split('/').at(-1) ?? '';
    expect(git.commands.map((command) => command[0])).toEqual([
      'check-ref-format',
      'worktree',
      'show-ref',
      'branch',
      'worktree',
      'status',
      'worktree',
      'merge',
      'branch',
    ]);
    expect(git.commands[3]).toEqual(['branch', branch, 'HEAD']);
    expect(git.commands.at(-3)).toEqual(['worktree', 'remove', target]);
    expect(git.commands.at(-2)).toEqual(['merge', '--ff-only', branch]);
    expect(git.commands.at(-1)).toEqual(['branch', '-d', branch]);
  });

  test('merge-to-head keeps the temporary branch unmerged on failure', async () => {
    const git = fixture();
    const failure = new Error('boom');

    const rejected = await rejectionOf(
      runtimeOver(git).open(REPO, new GitOptions({ rootPath: 'wt', strategy: new MergeToHeadStrategy() }), () => Promise.reject(failure)),
    );

    expect(rejected).toBe(failure);
    expect(git.commands.at(-1)?.slice(0, 2)).toEqual(['worktree', 'remove']);
    expect(git.commands.map((command) => command[0])).not.toContain('merge');
  });

  test('branch fetches and starts a new branch at the base branch', async () => {
    const git = fixture();
    let target = '';

    await runtimeOver(git).open(REPO, worktreeOptions(new BranchStrategy('loop/a', 'develop')), (path) => {
      target = path;
      return Promise.resolve();
    });

    expect(target).toBe('/repo/wt/loop/a');
    expect(git.commands[0]).toEqual(['fetch', '--all', '--prune']);
    expect(git.commands).toContainEqual(['branch', 'loop/a', 'develop']);
    expect(git.commands.at(-1)).toEqual(['worktree', 'remove', '/repo/wt/loop/a']);
    expect(git.commands.map((command) => command[0])).not.toContain('merge');
  });

  test('a branch without a base starts at HEAD', async () => {
    const git = fixture();

    await runtimeOver(git).open(REPO, worktreeOptions(new BranchStrategy('loop/a')), () => Promise.resolve());

    expect(git.commands).toContainEqual(['branch', 'loop/a', 'HEAD']);
  });

  test('the worktree is removed when the body fails', async () => {
    const git = fixture();

    const rejected = await rejectionOf(
      runtimeOver(git).open(REPO, worktreeOptions(new BranchStrategy('loop/a')), () => Promise.reject(new Error('boom'))),
    );

    expect(rejected).toMatchObject({ message: 'boom' });
    expect(git.commands.at(-1)).toEqual(['worktree', 'remove', '/repo/wt/loop/a']);
  });

  test('repositoryPath selects the git -C target', async () => {
    const git = fixture();
    const options = new GitOptions({ repositoryPath: 'services/api', rootPath: 'wt', strategy: new BranchStrategy('loop/a') });

    await runtimeOver(git).open(REPO, options, () => Promise.resolve());

    expect(git.repositories).toEqual(new Set(['/repo/services/api', '/repo/wt/loop/a']));
  });

  test('a root outside cwd is rejected for worktree strategies', async () => {
    for (const strategy of [new MergeToHeadStrategy(), new BranchStrategy('a')]) {
      const rejected = await rejectionOf(
        runtimeOver(fixture({})).open(REPO, new GitOptions({ rootPath: '../x', strategy }), () => Promise.resolve()),
      );

      expect(rejected).toBeInstanceOf(ValueError);
      expect(rejected).toMatchObject({ message: expect.stringContaining('must be inside') as unknown });
    }
  });
});

describe('creating a worktree', () => {
  const add = async (git: FakeGitProcesses, base?: string): Promise<void> => {
    await new GitCli(git).addWorktree(REPO, '/repo/.wt/a', 'a', base ?? null);
  };

  test('a new branch starts from the base when nothing exists', async () => {
    const git = fixture({ missing: ['refs/heads/', 'refs/remotes/'] });

    await new GitCli(git).addWorktree(REPO, '/repo/.wt/loop/a', 'loop/a', 'develop');

    expect(git.commands[0]).toEqual(['check-ref-format', '--branch', 'loop/a']);
    expect(git.commands.at(-2)).toEqual(['branch', 'loop/a', 'develop']);
    expect(git.commands.at(-1)).toEqual(['worktree', 'add', '/repo/.wt/loop/a', 'loop/a']);
  });

  test('a new branch defaults to HEAD', async () => {
    const git = fixture();

    await add(git);

    expect(git.commands.at(-2)).toEqual(['branch', 'a', 'HEAD']);
  });

  test('a remote-only branch starts from origin', async () => {
    const git = fixture({ missing: ['refs/heads/'] });

    await add(git);

    expect(git.commands.at(-2)).toEqual(['branch', 'a', 'origin/a']);
  });

  test('an existing local branch is reused as is', async () => {
    const git = fixture({});

    await add(git);

    expect(git.commands.map((command) => command[0])).not.toContain('branch');
    expect(git.commands.at(-1)).toEqual(['worktree', 'add', '/repo/.wt/a', 'a']);
  });
});

describe('Loop hooks', () => {
  test('validate their command and timeout', () => {
    expect(() => new WorktreeReadyLoopHook('')).toThrow(ValueError);
    expect(() => new WorktreeReadyLoopHook('x', 0)).toThrow(ValueError);
  });

  test('the point classes fix their point and have no instantiable base', () => {
    expect(new WorktreeReadyLoopHook('x').point).toBe('worktree-ready');
    expect(new WorktreeRemovingLoopHook('x').point).toBe('worktree-removing');
    expect(new RunFinishedLoopHook('x').point).toBe('run-finished');
    expect(Object.values(LoopHookPoint)).toEqual(['run-finished', 'worktree-ready', 'worktree-removing']);
    const base = LoopHookBase();
    expect(() => new base('x')).toThrow(TypeError);
  });

  test('the head strategy rejects hooks but worktree strategies accept them', () => {
    expect(() => new GitOptions({ loopHooks: [new WorktreeReadyLoopHook('x')] })).toThrow('need a worktree strategy');
    expect(hookOptions('x').loopHooks).toHaveLength(1);
    expect(new GitOptions({ loopHooks: [new RunFinishedLoopHook('x')], strategy: new MergeToHeadStrategy() }).loopHooks).toHaveLength(1);
  });

  test('hooksAt keeps the declared order', () => {
    const [a, b, c] = [new WorktreeReadyLoopHook('a'), new RunFinishedLoopHook('b'), new WorktreeReadyLoopHook('c')];
    const options = new GitOptions({ loopHooks: [a, b, c], strategy: new MergeToHeadStrategy() });

    expect(options.hooksAt(LoopHookPoint.WORKTREE_READY)).toEqual([a, c]);
    expect(options.hooksAt(LoopHookPoint.WORKTREE_REMOVING)).toEqual([]);
  });

  test('run in order after the worktree is added, with cwd, timeout and the LOOP variables', async () => {
    const git = fixture();
    let hooksSeenByBody = 0;

    await runtimeOver(git).open(REPO, hookOptions('one', 'two'), () => {
      hooksSeenByBody = git.hooks.length;
      return Promise.resolve();
    });

    expect(hooksSeenByBody).toBe(2);
    expect(git.hooks.map((hook) => hook.command)).toEqual(['one', 'two']);
    expect(git.names.indexOf('worktree add')).toBeLessThan(git.names.indexOf('hook one'));
    for (const { options } of git.hooks) {
      expect(options?.cwd).toBe('/repo/wt/loop/a');
      expect(options?.timeoutSec).toBe(5);
      expect(options?.env).toEqual({ LOOP_HOOK_POINT: 'worktree-ready', LOOP_REPOSITORY: '/repo', LOOP_WORKTREE: '/repo/wt/loop/a' });
    }
  });

  test('a failing hook removes a clean worktree and skips the body', async () => {
    const git = fixture({ failing: ['one'], missing: ['refs/'] });
    let entered = false;

    const rejected = await rejectionOf(
      runtimeOver(git).open(REPO, hookOptions('one', 'two'), () => {
        entered = true;
        return Promise.resolve();
      }),
    );

    expect(entered).toBe(false);
    expect(rejected).toBeInstanceOf(LoopHookError);
    expect(rejected).toMatchObject({ command: 'one', output: 'out-err', point: 'worktree-ready' });
    expect((rejected as Error).message).toContain('worktree-ready hook failed');
    expect(git.hooks.map((hook) => hook.command)).toEqual(['one']);
    expect(git.commands.at(-1)).toEqual(['worktree', 'remove', '/repo/wt/loop/a']);
    for (const forbidden of ['diff', 'commit', 'merge']) {
      expect(git.commands.map((command) => command[0])).not.toContain(forbidden);
    }
  });

  test('a timed-out hook raises a LoopHookError that names the timeout', async () => {
    const rejected = await rejectionOf(
      runtimeOver(fixture({ missing: ['refs/'], timingOut: ['slow'] })).open(REPO, hookOptions('slow'), () => Promise.resolve()),
    );

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect((rejected as Error).message).toContain('timed out after 5s');
  });

  test('a failing hook under merge-to-head does not merge', async () => {
    const git = fixture({ failing: ['one'], missing: ['refs/'] });
    const options = worktreeOptions(new MergeToHeadStrategy(), new WorktreeReadyLoopHook('one'));

    const rejected = await rejectionOf(runtimeOver(git).open(REPO, options, () => Promise.resolve()));

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect(git.commands.map((command) => command[0])).not.toContain('merge');
    expect(git.names).not.toContain('branch -d');
  });

  test('a builder with a failing hook never starts the agent, per run or in a workspace', async () => {
    const git = fixture({ failing: ['one'], missing: ['refs/'] });
    const runner = new RecordingRunner();
    const builder = new AgentBuilder(runner, runtimeOver(git), new RecordingDocker([]), new AgentContext({ cwd: REPO })).withGit(
      hookOptions('one'),
    );

    expect(await rejectionOf(builder.create().run(new AgentRequest('x')))).toBeInstanceOf(LoopHookError);
    expect(await rejectionOf(builder.open(() => Promise.resolve()))).toBeInstanceOf(LoopHookError);
    expect(runner.contexts).toEqual([]);
  });

  test('fire once per run() for a client and once for a whole workspace', async () => {
    const git = fixture();
    const builder = new AgentBuilder(
      new RecordingRunner(),
      runtimeOver(git),
      new RecordingDocker([]),
      new AgentContext({ cwd: REPO }),
    ).withGit(hookOptions('one', 'two'));

    const client = builder.create();
    await client.run(new AgentRequest('a'));
    await client.run(new AgentRequest('b'));
    expect(git.hooks).toHaveLength(4);
    git.hooks.length = 0;
    await builder.open(async (workspace) => {
      await workspace.agent().run(new AgentRequest('a'));
      await workspace.agent().run(new AgentRequest('b'));
    });

    expect(git.hooks.map((hook) => hook.command)).toEqual(['one', 'two']);
  });
});

describe('the worktree and run scopes', () => {
  const mergeOptions = (...hooks: LoopHook[]): GitOptions => new GitOptions({ loopHooks: hooks, rootPath: 'wt', strategy: new MergeToHeadStrategy() });

  test('a successful body returns its result; removal hooks, merge and run-finished follow in order', async () => {
    const git = fixture();
    const options = mergeOptions(new WorktreeRemovingLoopHook('bye'), new RunFinishedLoopHook('done'));

    const result = await runtimeOver(git).open(REPO, options, () => Promise.resolve('value'));

    expect(result).toBe('value');
    const names = git.names;
    expect(names.indexOf('hook bye')).toBeLessThan(names.indexOf('status --porcelain'));
    expect(names.indexOf('status --porcelain')).toBeLessThan(names.indexOf('worktree remove'));
    expect(names.indexOf('worktree remove')).toBeLessThan(names.indexOf('merge --ff-only'));
    expect(names.indexOf('branch -d')).toBeLessThan(names.indexOf('hook done'));
    expect(git.hooks.filter((hook) => hook.command === 'done')).toHaveLength(1);
  });

  test('removal hooks run in the worktree and run-finished in the repository, with the worktree in LOOP_WORKTREE', async () => {
    const git = fixture();
    let target = '';

    await runtimeOver(git).open(REPO, mergeOptions(new WorktreeRemovingLoopHook('bye'), new RunFinishedLoopHook('done')), (path) => {
      target = path;
      return Promise.resolve();
    });

    const [bye, done] = git.hooks;
    expect(bye?.options?.cwd).toBe(target);
    expect(bye?.options?.env?.LOOP_HOOK_POINT).toBe('worktree-removing');
    expect(done?.options?.cwd).toBe(REPO);
    expect(done?.options?.env).toMatchObject({ LOOP_HOOK_POINT: 'run-finished', LOOP_WORKTREE: target });
    expect(git.commands.map((command) => command[0])).not.toContain('commit');
  });

  test('a failing removal hook still removes the worktree and skips merge and run-finished', async () => {
    const git = fixture({ failing: ['bye'], missing: ['refs/'] });
    const options = mergeOptions(new WorktreeRemovingLoopHook('bye'), new RunFinishedLoopHook('done'));

    const rejected = await rejectionOf(runtimeOver(git).open(REPO, options, () => Promise.resolve()));

    expect(rejected).toMatchObject({ point: 'worktree-removing' });
    expect(git.names).toContain('worktree remove');
    expect(git.names).not.toContain('merge --ff-only');
    expect(git.hooks.map((hook) => hook.command)).toEqual(['bye']);
  });

  test('a failing run-finished hook raises after the merge', async () => {
    const git = fixture({ failing: ['done'], missing: ['refs/'] });

    const rejected = await rejectionOf(runtimeOver(git).open(REPO, mergeOptions(new RunFinishedLoopHook('done')), () => Promise.resolve()));

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect(git.names).toContain('branch -d');
  });

  const failures: [string, () => unknown][] = [
    ['an ordinary error', () => new Error('boom')],
    ['a cancellation', () => new Cancelled('SIGINT')],
    ['a non-error value', () => 'plain'],
  ];

  for (const [label, failure] of failures) {
    test(`${label}: the original is rethrown, no removal hook, merge or run-finished fires, a clean worktree is removed`, async () => {
      const git = fixture();
      const thrown = failure();
      const options = mergeOptions(new WorktreeRemovingLoopHook('bye'), new RunFinishedLoopHook('done'));

      const rejected = await rejectionOf(runtimeOver(git).open(REPO, options, throwing(thrown)));

      expect(rejected).toBe(thrown);
      expect(git.hooks).toEqual([]);
      expect(git.names).toContain('worktree remove');
      expect(git.names).not.toContain('merge --ff-only');
    });

    test(`${label}: a dirty worktree is kept and the original is rethrown`, async () => {
      const git = fixture({ dirty: true, missing: ['refs/'] });
      const thrown = failure();

      const rejected = await rejectionOf(runtimeOver(git).open(REPO, mergeOptions(), throwing(thrown)));

      expect(rejected).toBe(thrown);
      expect(git.names).not.toContain('worktree remove');
    });
  }

  test('a dirty worktree after a successful body is kept and reported, never merged', async () => {
    const git = fixture({ dirty: true, missing: ['refs/'] });

    const rejected = await rejectionOf(
      runtimeOver(git).open(REPO, mergeOptions(new RunFinishedLoopHook('done')), () => Promise.resolve('lost')),
    );

    expect(rejected).toBeInstanceOf(WorktreeError);
    expect((rejected as Error).message).toContain('uncommitted changes; preserved at');
    expect(git.names).not.toContain('worktree remove');
    expect(git.names).not.toContain('merge --ff-only');
    expect(git.hooks).toEqual([]);
  });
});

describe('cleanup failures', () => {
  let logged: { level: string; message: string }[] = [];
  let previous: ReturnType<typeof setLogSink> = null;

  beforeEach(() => {
    logged = [];
    previous = setLogSink((record) => logged.push({ level: record.level, message: record.message }));
  });

  afterEach(() => {
    setLogSink(previous);
  });

  test('while an error propagates, the original is rethrown, the failure is logged and attached to its notes', async () => {
    const git = fixture({ failing: ['one'], missing: ['refs/'], removeFails: true });

    const rejected = await rejectionOf(runtimeOver(git).open(REPO, hookOptions('one'), () => Promise.resolve()));

    expect(rejected).toBeInstanceOf(LoopHookError);
    expect(rejected).toMatchObject({ command: 'one', point: 'worktree-ready' });
    const [note] = notesOf(rejected);
    expect(note).toContain('worktree cleanup also failed; retained /repo/wt/loop/a');
    expect(logged.filter((record) => record.level === 'error').map((record) => record.message)).toEqual([note ?? '']);
  });

  test('a body error keeps its identity when cleanup also fails', async () => {
    const failure = new Error('original failure');

    const rejected = await rejectionOf(
      runtimeOver(fixture({ missing: ['refs/'], removeFails: true })).open(REPO, hookOptions(), () => Promise.reject(failure)),
    );

    expect(rejected).toBe(failure);
    expect(notesOf(failure)).toHaveLength(1);
    expect(Object.keys(failure)).not.toContain('notes');
  });

  test('a thrown value that cannot carry notes is still rethrown and the failure is logged', async () => {
    const rejected = await rejectionOf(
      runtimeOver(fixture({ missing: ['refs/'], removeFails: true })).open(REPO, hookOptions(), throwing('plain')),
    );

    expect(rejected).toBe('plain');
    expect(logged.some((record) => record.level === 'error' && record.message.includes('cleanup also failed'))).toBe(true);
  });

  test('after a successful body, the cleanup failure is raised, not swallowed', async () => {
    const git = fixture({ missing: ['refs/'], removeFails: true });

    const rejected = await rejectionOf(runtimeOver(git).open(REPO, hookOptions(), () => Promise.resolve('done')));

    expect(rejected).toMatchObject({ name: 'ProcessFailedError' });
    expect(git.hooks).toEqual([]);
  });

  test('a retained dirty worktree is logged as a warning', async () => {
    const git = fixture({ dirty: true, missing: ['refs/'] });

    await rejectionOf(runtimeOver(git).open(REPO, hookOptions(), () => Promise.reject(new Error('x'))));

    expect(logged).toContainEqual({ level: 'warning', message: 'dirty worktree retained: /repo/wt/loop/a' });
  });
});

function LoopHookBase(): new (command: string) => LoopHook {
  return Object.getPrototypeOf(WorktreeReadyLoopHook) as new (command: string) => LoopHook;
}

/** A body that throws `value` synchronously, so the thrown value can be of any type. */
function throwing(value: unknown): () => never {
  return () => {
    throw value;
  };
}
