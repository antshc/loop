import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

import { Cancelled, ProcessFailedError } from '../../../../src/loop/index.ts';
import { Blocked, processLock, PrState } from '../../infrastructure/state.ts';
import { run, type RunDependencies } from '../../workflow/run.ts';
import { comment, git, LocalGit, Remote, type Scenario, scenario, scriptedAgents, thread } from '../support/remote.ts';

let world: Scenario;

beforeEach(() => {
  world = scenario();
});

afterEach(() => {
  rmSync(world.root, { force: true, recursive: true });
});

const worktree = (): string => join(world.settings.stateDir, 'worktrees', 'babysit', 'a', 'b', '1');
const state = (): PrState => new PrState(world.settings.stateDir, 'a/b', 1);

/** Runs the Workflow until the first wait, when the PR closes and the run completes. */
function execute(
  remote: Remote,
  agents: ReturnType<typeof scriptedAgents>,
  extra: RunDependencies = {},
): Promise<number> {
  return run(world.settings, 'a/b', 1, {
    git: new LocalGit(),
    github: remote,
    newAgent: agents.newAgent,
    wait: () => {
      remote.closed = true;
      return Promise.resolve();
    },
    ...extra,
  });
}

describe('address review feedback', () => {
  test('verifies, commits, pushes and replies exactly once', async () => {
    const remote = new Remote(world.bare, world.base);
    const agents = scriptedAgents();

    expect(await execute(remote, agents)).toBe(0);

    expect(agents.modes).toEqual(['group', 'resolve']);
    expect(remote.replies).toEqual(['Fixed!']);
    expect(git(world.bare, 'rev-parse', 'feature')).not.toBe(world.base);
    expect(git(world.checkout, 'rev-parse', 'HEAD')).toBe(world.base);
    expect(existsSync(worktree())).toBe(false);
    expect(state().data.status).toBe('completed');
    expect(Object.values(state().data.items).every((item) => (item as { status: string }).status === 'handled')).toBe(true);
  });

  test('a push failure keeps the checkpoint and never replies; recovery publishes without another agent call', async () => {
    class RejectPush extends LocalGit {
      override publish(): Promise<void> {
        return Promise.reject(new ProcessFailedError({ argv: ['git', 'push'], exitCode: 1, signal: null, stderr: '', stdout: '' }));
      }
    }
    const remote = new Remote(world.bare, world.base);
    const agents = scriptedAgents();

    expect(await execute(remote, agents, { git: new RejectPush() })).toBe(1);

    expect(remote.replies).toEqual([]);
    const batch = state().data.batch as { head: string };
    expect(batch.head).not.toBe(world.base);
    expect(Object.values(state().data.items).every((item) => (item as { status: string }).status === 'pending')).toBe(true);
    expect(existsSync(worktree())).toBe(false);
    expect(git(world.checkout, 'rev-parse', 'babysit/a/b/1')).toBe(batch.head);

    expect(await execute(remote, agents)).toBe(0);
    expect(agents.modes).toEqual(['group', 'resolve']);
    expect(remote.replies).toEqual(['Fixed!']);
  });

  test('an invalid agent report does not publish', async () => {
    const remote = new Remote(world.bare, world.base);

    expect(await execute(remote, scriptedAgents({ invalid: true }))).toBe(1);

    expect(remote.replies).toEqual([]);
    expect(git(world.bare, 'rev-parse', 'feature')).toBe(world.base);
    expect(state().data.reason).toBe('address returned invalid JSON');
  });

  test('failed verification keeps the edits and neither commits nor replies', async () => {
    const remote = new Remote(world.bare, world.base);
    const failAfterEdit = (path: string): Promise<void> =>
      readFileSync(join(path, 'cart.py'), 'utf8') === 'fixed = True\n'
        ? Promise.reject(new ProcessFailedError({ argv: ['test'], exitCode: 1, signal: null, stderr: '', stdout: '' }))
        : Promise.resolve();

    expect(await execute(remote, scriptedAgents(), { verification: failAfterEdit })).toBe(1);

    expect(git(world.bare, 'rev-parse', 'feature')).toBe(world.base);
    expect(remote.replies).toEqual([]);
    expect(readFileSync(join(worktree(), 'cart.py'), 'utf8')).toBe('fixed = True\n');
  });

  test('a green PR receives late feedback without stopping', async () => {
    const remote = new Remote(world.bare, world.base, { reviews: false });
    const agents = scriptedAgents();
    let ticks = 0;

    const code = await run(world.settings, 'a/b', 1, {
      git: new LocalGit(),
      github: remote,
      newAgent: agents.newAgent,
      wait: () => {
        ticks += 1;
        if (ticks === 1) remote.discussions = [thread()];
        else remote.closed = true;
        return Promise.resolve();
      },
    });

    expect(code).toBe(0);
    expect(remote.replies).toEqual(['Fixed!']);
    expect(agents.modes).toEqual(['group', 'resolve']);
  });
});

describe('maintain CI', () => {
  test('the maintainer fixes and the workflow publishes', async () => {
    const remote = new Remote(world.bare, world.base, { ciFailure: true, reviews: false });
    const agents = scriptedAgents();

    expect(await execute(remote, agents)).toBe(0);

    expect(agents.modes).toEqual(['diagnose', 'fix']);
    expect(git(world.bare, 'rev-parse', 'feature')).not.toBe(world.base);
    expect(remote.replies).toEqual([]);
  });

  test('a transient failure uses the per-SHA retry budget', async () => {
    const remote = new Remote(world.bare, world.base, { ciFailure: true, reviews: false });

    expect(await execute(remote, scriptedAgents({ transient: true }))).toBe(0);

    expect(remote.retries).toEqual([1]);
    expect((state().data.retries as Record<string, number>)[world.base]).toBe(1);
  });

  test('retry exhaustion blocks without another rerun', async () => {
    const prior = state();
    prior.data.retries[world.base] = world.settings.retries;
    prior.save();
    const remote = new Remote(world.bare, world.base, { ciFailure: true, reviews: false });

    expect(await execute(remote, scriptedAgents({ transient: true }))).toBe(1);

    expect(remote.retries).toEqual([]);
    expect(state().data.status).toBe('blocked');
  });

  test('a repeated stale CI snapshot spends neither another retry nor another agent call', async () => {
    const remote = new Remote(world.bare, world.base, { ciFailure: true, reviews: false });
    const agents = scriptedAgents({ transient: true });
    let ticks = 0;

    const code = await run(world.settings, 'a/b', 1, {
      git: new LocalGit(),
      github: remote,
      newAgent: agents.newAgent,
      wait: () => {
        ticks += 1;
        if (ticks === 3) remote.closed = true;
        return Promise.resolve();
      },
    });

    expect(code).toBe(0);
    expect(remote.retries).toEqual([1]);
    expect(agents.modes).toEqual(['diagnose']);
  });
});

describe('interruption and locks', () => {
  test('a stop request records interrupted and returns 130', async () => {
    const remote = new Remote(world.bare, world.base, { reviews: false });

    const code = await run(world.settings, 'a/b', 1, { git: new LocalGit(), github: remote, stop: () => true });

    expect(code).toBe(130);
    expect(state().data.status).toBe('interrupted');
  });

  test('a cancelled run records interrupted, rethrows and releases its locks', async () => {
    const remote = new Remote(world.bare, world.base, { reviews: false });
    const cancelled = new Cancelled('SIGTERM');

    const failure = await run(world.settings, 'a/b', 1, {
      checkpoint: () => {
        throw cancelled;
      },
      git: new LocalGit(),
      github: remote,
    }).catch((error: unknown) => error);

    expect(failure).toBe(cancelled);
    expect(state().data.status).toBe('interrupted');
    expect(await processLock(state().lockPath, () => 'free')).toBe('free');
  });

  test('a second worker for the same PR is Blocked and leaves the state untouched', async () => {
    const remote = new Remote(world.bare, world.base);

    const failure = await processLock(state().lockPath, () =>
      execute(remote, scriptedAgents()).catch((error: unknown) => error),
    );

    expect(failure).toBeInstanceOf(Blocked);
    expect((failure as Error).message).toBe(`Another process owns ${state().lockPath}`);
    expect(remote.replies).toEqual([]);
  });

  test('a PR from a fork or another author is Blocked', async () => {
    const remote = new Remote(world.bare, world.base, { reviews: false });
    const original = remote.snapshot.bind(remote);
    remote.snapshot = async (repo, number) => {
      const snapshot = await original(repo, number);
      return { ...snapshot, pr: { ...snapshot.pr, head: { ...snapshot.pr.head, repo: { full_name: 'fork/b' } } } };
    };

    expect(await execute(remote, scriptedAgents())).toBe(1);

    expect(state().data.reason).toBe('PR author changed or fork head is not supported by this checkout');
  });

  test('a blocked run reconciles on the latest snapshot so its own replies do not retrigger it', async () => {
    const remote = new Remote(world.bare, world.base, { reviews: false });
    remote.discussions = [thread([comment('c1', 'reviewer', 'Need clarification')])];
    const agents = scriptedAgents({ invalid: true });

    expect(await execute(remote, agents)).toBe(1);

    expect(state().data.status).toBe('blocked');
    expect(typeof state().data.fingerprint).toBe('string');
    expect(state().data.fingerprint).not.toBe('');
  });
});
