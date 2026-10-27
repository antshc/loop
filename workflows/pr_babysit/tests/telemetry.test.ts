import { afterEach, beforeEach, expect, test } from 'bun:test';
import { rmSync } from 'node:fs';

import type { Observer } from '../workflow/maintenance.ts';

import { SpanRecorder } from '../../../tests/unit/support/span-observer.ts';
import { run } from '../workflow/run.ts';
import { LocalGit, Remote, type Scenario, scenario, scriptedAgents } from './support/remote.ts';

class RecordingObserver extends SpanRecorder implements Observer {
  agentRun(): void {
    // Agent Run facts are not under test here.
  }

  worktree(): void {
    // Worktree facts are not under test here.
  }
}

let world: Scenario;

beforeEach(() => {
  world = scenario();
});

afterEach(() => {
  rmSync(world.root, { force: true, recursive: true });
});

test('each babysit execution is its own root trace and quiet polls export nothing', async () => {
  const observer = new RecordingObserver();
  for (let execution = 0; execution < 2; execution++) {
    const remote = new Remote(world.bare, world.base, { reviews: false });
    const code = await run(world.settings, 'a/b', 1, {
      git: new LocalGit(),
      github: remote,
      newAgent: scriptedAgents().newAgent,
      observer,
      wait: () => {
        remote.closed = true;
        return Promise.resolve();
      },
    });
    expect(code).toBe(0);
  }

  const roots = observer.spans.filter((span) => span.name === 'babysit');
  expect(roots).toHaveLength(2);
  expect(roots.every((span) => span.parent === null)).toBe(true);
  expect(new Set(observer.spans.map((span) => span.name))).toEqual(new Set(['babysit', 'PR readiness changed']));
});
