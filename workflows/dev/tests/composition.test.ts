import { afterEach, describe, expect, test } from 'bun:test';
import { resolve } from 'node:path';

import { Agent, type SessionLogReader } from '../../../src/loop/index.ts';
import { parseCommandLine } from '../args.ts';
import { CliGitService } from '../checkout.ts';
import { composeDev } from '../composition.ts';
import { type DevDependencies, LOG_DIR_NAME, LOOP_HOOKS as SHIPPED_HOOKS } from '../dependencies.ts';
import { DRY_TARGET, DryGh, DryGit } from '../dry.ts';
import { FileExecutionStore } from '../journal.ts';
import { main } from '../main.ts';
import { GitHubRepo, TicketsTracker } from '../platforms.ts';
import { DevRunner } from '../runner.ts';
import {
  AGENT_FACTORY,
  CLOCK,
  CURRENT_DIRECTORY,
  EXECUTION_STORE,
  GIT_SERVICE,
  GITHUB_FACTORY,
  LOG_DIRECTORY,
  LOOP_HOOKS,
  OBSERVER_FACTORY,
  REPOSITORIES,
  SESSION_LOG_READERS,
  TICKETS_TRACKER,
} from '../tokens.ts';
import { silenceLogging } from './support/observer.ts';
import { Stream } from './support/stream.ts';

afterEach(() => {
  silenceLogging();
});

function compose(argv: readonly string[], dependencies: DevDependencies = {}): ReturnType<typeof composeDev> {
  const stream = new Stream();
  const options = parseCommandLine(argv, { env: {}, stderr: stream, stdout: stream });
  if (typeof options === 'number') {
    throw new TypeError(`bad arguments: ${stream.text}`);
  }
  return composeDev(options, dependencies, stream);
}

describe('the container without overrides', () => {
  test('binds the shipped implementation to every seam token', () => {
    const container = compose([]);

    expect(container.get(GIT_SERVICE)).toBeInstanceOf(CliGitService);
    expect(container.get(EXECUTION_STORE)).toBeInstanceOf(FileExecutionStore);
    expect(container.get(TICKETS_TRACKER)).toBeInstanceOf(TicketsTracker);
    expect(container.get(AGENT_FACTORY)).toBe(Agent);
    expect(container.get(LOOP_HOOKS)).toBe(SHIPPED_HOOKS);
    expect(container.get(GITHUB_FACTORY)).toBeNull();
    expect(container.get(SESSION_LOG_READERS)).toBeNull();
    expect(container.get<readonly unknown[]>(REPOSITORIES)).toHaveLength(1);
    expect(container.get(DevRunner)).toBeInstanceOf(DevRunner);
  });

  test('resolves the log directory from the working directory', () => {
    expect(compose([]).get(LOG_DIRECTORY)).toBe(resolve(process.cwd(), LOG_DIR_NAME));
    expect(compose(['--log-dir', 'out']).get(LOG_DIRECTORY)).toBe(resolve(process.cwd(), 'out'));
  });

  test('shares one instance of each service', () => {
    const container = compose([]);

    expect(container.get(GIT_SERVICE)).toBe(container.get(GIT_SERVICE));
    expect(container.get(EXECUTION_STORE)).toBe(container.get(EXECUTION_STORE));
    expect(container.get(DevRunner)).toBe(container.get(DevRunner));
  });

  test('`--fake` replaces the shipped implementation of that seam only', () => {
    const container = compose(['--fake', 'agent', '--fake', 'git']);

    expect(container.get(GIT_SERVICE)).toBeInstanceOf(DryGit);
    expect(container.get(EXECUTION_STORE)).toBeInstanceOf(FileExecutionStore);
    expect(container.get<null | readonly unknown[]>(SESSION_LOG_READERS)).toHaveLength(1);
    expect(container.get(GITHUB_FACTORY)).toBeNull();
  });
});

describe('the container with overrides', () => {
  test('rebinds each overridden token to the given value', () => {
    const git = new DryGit();
    const store = new FileExecutionStore('/work/store');
    const githubFactory = (): GitHubRepo => new GitHubRepo('o', 'r', () => Promise.resolve(''));
    const clock = (): number => 7;
    const cwd = (): string => '/work';
    const newAgent = (): ReturnType<typeof Agent> => Agent();
    const readers: readonly SessionLogReader[] = [];
    const repositories = [] as const;
    const loopHooks = [] as const;
    const newObserver = (): never => {
      throw new Error('not called');
    };

    const container = compose([], { clock, cwd, git, githubFactory, loopHooks, newAgent, newObserver, readers, repositories, store });

    expect(container.get(GIT_SERVICE)).toBe(git);
    expect(container.get(EXECUTION_STORE)).toBe(store);
    expect(container.get(GITHUB_FACTORY)).toBe(githubFactory);
    expect(container.get(CLOCK)).toBe(clock);
    expect(container.get(CURRENT_DIRECTORY)).toBe(cwd);
    expect(container.get(AGENT_FACTORY)).toBe(newAgent);
    expect(container.get(SESSION_LOG_READERS)).toBe(readers);
    expect(container.get(REPOSITORIES)).toBe(repositories);
    expect(container.get(LOOP_HOOKS)).toBe(loopHooks);
    expect(container.get(OBSERVER_FACTORY)).toBe(newObserver);
    expect(container.get(LOG_DIRECTORY)).toBe(resolve('/work', LOG_DIR_NAME));
  });

  test('wins over a `--fake` seam without building the fake', () => {
    const git = new DryGit();

    const container = compose(['--fake', 'agent', '--fake', 'git', '--fake', 'store'], { git });

    expect(container.get(GIT_SERVICE)).toBe(git);
  });

  test('leaves the other tokens on their shipped implementation', () => {
    const container = compose([], { git: new DryGit() });

    expect(container.get(EXECUTION_STORE)).toBeInstanceOf(FileExecutionStore);
    expect(container.get(AGENT_FACTORY)).toBe(Agent);
  });

  test('is used by the run: the overridden GitHub factory serves the tracker', async () => {
    const gh = new DryGh();
    const stream = new Stream();

    const code = await main(['--fake', 'agent', '--fake', 'git', '--fake', 'store', '--fake-speed', '0', '--no-otel'], {
      clock: () => 0,
      env: {},
      githubFactory: () => new GitHubRepo(DRY_TARGET.owner, DRY_TARGET.repo, gh.run),
      stderr: stream,
      stdout: stream,
    });

    expect(code).toBe(0);
    expect(gh.calls.length).toBeGreaterThan(0);
  });
});
