import { afterEach, beforeEach, expect, test } from 'bun:test';
import { existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';

import type { Maintenance } from '../../workflow/maintenance.ts';

import { digest } from '../../infrastructure/github.ts';
import { Blocked, PrState } from '../../infrastructure/state.ts';
import { openWorkspace } from '../../workflow/workspace.ts';
import { LocalGit, Remote, type Scenario, scenario, scriptedAgents } from '../support/remote.ts';

let world: Scenario;

beforeEach(() => {
  world = scenario();
});

afterEach(() => {
  rmSync(world.root, { force: true, recursive: true });
});

function context(): Maintenance {
  // openWorkspace only needs these members of the Maintenance.
  return {
    git: new LocalGit(),
    newAgent: scriptedAgents().newAgent,
    observer: null,
    repository: world.settings.repository('a/b'),
    settings: world.settings,
    state: new PrState(world.settings.stateDir, 'a/b', 1),
  } as unknown as Maintenance;
}

test('a named worktree is serialized by the workflow: a second opener cannot edit it', async () => {
  const snapshot = await new Remote(world.bare, world.base).snapshot('a/b', 1);
  const first = context();
  const lockPath = join(
    world.settings.stateDir,
    'locks',
    `${digest([world.checkout, 'babysit/a/b/1'])}.lock`,
  );

  await openWorkspace(first, snapshot, async (workspace) => {
    expect(existsSync(workspace.path)).toBe(true);
    expect(existsSync(lockPath)).toBe(true);

    const failure = await openWorkspace(context(), snapshot, () => Promise.resolve('duplicate owner')).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(Blocked);
    expect((failure as Error).message).toBe(`Another process owns ${lockPath}`);
    expect(existsSync(workspace.path)).toBe(true);
  });
});

test('the worktree is removed when clean and the lock is released afterwards', async () => {
  const snapshot = await new Remote(world.bare, world.base).snapshot('a/b', 1);
  let path = '';

  await openWorkspace(context(), snapshot, (workspace) => {
    path = workspace.path;
    return Promise.resolve();
  });

  expect(existsSync(path)).toBe(false);
  expect(await openWorkspace(context(), snapshot, () => Promise.resolve('again'))).toBe('again');
});

test('unexpected local commits on the recovery branch block the run', async () => {
  const snapshot = await new Remote(world.bare, world.base).snapshot('a/b', 1);
  await openWorkspace(context(), snapshot, () => Promise.resolve());
  Bun.spawnSync(['git', '-C', world.checkout, 'branch', '-f', 'babysit/a/b/1', world.base]);
  Bun.spawnSync(['git', '-C', world.checkout, 'worktree', 'prune']);
  const stray = Bun.spawnSync(
    ['git', '-C', world.checkout, 'commit-tree', `${world.base}^{tree}`, '-p', world.base, '-m', 'stray'],
    { stdout: 'pipe' },
  )
    .stdout.toString()
    .trim();
  Bun.spawnSync(['git', '-C', world.checkout, 'branch', '-f', 'babysit/a/b/1', stray]);

  const failure = await openWorkspace(context(), snapshot, () => Promise.resolve()).catch((error: unknown) => error);

  expect(failure).toBeInstanceOf(Blocked);
  expect((failure as Error).message).toBe('Unexpected local commits; inspect recovery branch');
});
