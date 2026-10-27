import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { CancelSignal, ProcessResult, ProcessRunner } from '../../../src/loop/index.ts';
import type { WorkflowGit } from '../infrastructure/git.ts';
import type { GithubService } from '../infrastructure/github.ts';
import type { Settings } from '../settings.ts';

import { Cancelled } from '../../../src/loop/index.ts';
import { createContainer } from '../composition.ts';
import { type DiscoveryService, PrDiscovery } from '../discovery.ts';
import { Git } from '../infrastructure/git.ts';
import { Github } from '../infrastructure/github.ts';
import { type SignalSource, StopRequest } from '../infrastructure/signals.ts';
import { Blocked } from '../infrastructure/state.ts';
import { main } from '../main.ts';
import { TOKENS } from '../tokens.ts';
import { PrWorker, refusingAfter, type WorkerService } from '../worker.ts';

const PR = 'https://github.com/a/b/pull/7';

class FakeSignals implements SignalSource {
  readonly #listeners = new Map<CancelSignal, Set<() => void>>();

  count(): number {
    return [...this.#listeners.values()].reduce((total, listeners) => total + listeners.size, 0);
  }

  emit(signal: CancelSignal): void {
    for (const listener of [...(this.#listeners.get(signal) ?? [])]) listener();
  }

  off(signal: CancelSignal, listener: () => void): void {
    this.#listeners.get(signal)?.delete(listener);
  }

  on(signal: CancelSignal, listener: () => void): void {
    this.#listeners.set(signal, (this.#listeners.get(signal) ?? new Set()).add(listener));
  }
}

let root: string;
let config: string;

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-composition-')));
  config = join(root, 'settings.json');
  writeFileSync(
    config,
    JSON.stringify({
      author: 'bot',
      repositories: [{ build: ['b'], name: 'a/b', path: 'x', test: ['t'] }],
      state_dir: 'state',
    }),
  );
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

function githubDouble(overrides: Partial<GithubService>): GithubService {
  return {
    login: unused,
    logs: unused,
    openPrs: unused,
    pullRequest: unused,
    reply: unused,
    requestCopilot: unused,
    retry: unused,
    snapshot: unused,
    ...overrides,
  };
}

function recordingRunner(): { commands: string[][]; runner: ProcessRunner } {
  const commands: string[][] = [];
  const runner: ProcessRunner = {
    run: (argv) => {
      commands.push([...argv]);
      const result: ProcessResult = {
        argv,
        exitCode: 0,
        signal: null,
        stderr: '',
        stdout: JSON.stringify({ login: 'someone' }),
      };
      return Promise.resolve(result);
    },
    start: () => {
      throw new Error('nothing is started in the background');
    },
  };
  return { commands, runner };
}

function unused(): never {
  throw new Error('not used by this test');
}

describe('default composition', () => {
  test('binds the shipped implementations to the seam tokens', () => {
    const signals = new FakeSignals();
    const stop = new StopRequest({ signals });
    const container = createContainer({ stop });

    expect(container.get(TOKENS.GithubService)).toBeInstanceOf(Github);
    expect(container.get(TOKENS.WorkflowGit)).toBeInstanceOf(Git);
    expect(container.get(TOKENS.WorkerService)).toBeInstanceOf(PrWorker);
    expect(container.get(TOKENS.DiscoveryService)).toBeInstanceOf(PrDiscovery);
    expect(typeof container.get(TOKENS.Verification)).toBe('function');
    expect(container.get(TOKENS.StopRequest)).toBe(stop);
    stop.dispose();
  });

  test('shares singletons and gives each adapter of the process runner its own instance', () => {
    const signals = new FakeSignals();
    const container = createContainer({ stop: new StopRequest({ signals }) });

    expect(container.get(TOKENS.WorkerService)).toBe(container.get(TOKENS.WorkerService));
    expect(container.get(TOKENS.DiscoveryService)).toBe(container.get(TOKENS.DiscoveryService));
    expect(container.get(TOKENS.GithubService)).not.toBe(container.get(TOKENS.GithubService));
  });

  test('the worker runner starts no child once the operator asked to stop', async () => {
    const signals = new FakeSignals();
    const stop = new StopRequest({ exit: () => undefined, signals });
    const { commands, runner } = recordingRunner();
    const guarded = refusingAfter(stop, runner);

    await guarded.run(['first']);
    signals.emit('SIGINT');

    const refused = await guarded.run(['second']).catch((error: unknown) => error);
    expect(refused).toBeInstanceOf(Cancelled);
    expect(() => guarded.start(['third'])).toThrow(Cancelled);
    expect(commands).toEqual([['first']]);
  });
});

describe('overrides rebind tokens', () => {
  test('each seam override replaces the binding of its token', () => {
    const github = githubDouble({});
    const git = { validate: unused } as unknown as WorkflowGit;
    const { runner } = recordingRunner();
    const container = createContainer({ git, github, processRunner: runner });

    expect(container.get(TOKENS.GithubService)).toBe(github);
    expect(container.get(TOKENS.WorkflowGit)).toBe(git);
    expect(container.get(TOKENS.ProcessRunner)).toBe(runner);
  });

  test('a process runner override reaches GitHub, Git and verification', async () => {
    const { commands, runner } = recordingRunner();
    const container = createContainer({ processRunner: runner });

    expect(await container.get<GithubService>(TOKENS.GithubService).login()).toBe('someone');
    await container.get<WorkflowGit>(TOKENS.WorkflowGit).clean('/repo');
    const verify = container.get<(path: string, repository: { build: string[]; test: string[] }, timeout: number) => Promise<void>>(
      TOKENS.Verification,
    );
    await verify('/repo', { build: ['build-it'], test: ['test-it'] }, 5);

    expect(commands[0]?.slice(0, 2)).toEqual(['gh', 'api']);
    expect(commands.some((command) => command[0] === 'git')).toBe(true);
    expect(commands.slice(-2)).toEqual([['build-it'], ['test-it']]);
  });

  test('a run override replaces the worker and a serve override replaces discovery', async () => {
    const container = createContainer({
      run: (_settings: Settings, repo, number) => Promise.resolve(number + repo.length),
      serve: (_settings: Settings, path) => Promise.resolve(path.length),
    });

    const settings = undefined as unknown as Settings;
    expect(await container.get<WorkerService>(TOKENS.WorkerService).run(settings, 'a/b', 7)).toBe(10);
    expect(
      await container
        .get<DiscoveryService>(TOKENS.DiscoveryService)
        .serve(settings, 'abcd', { includeExisting: false, retry: undefined }),
    ).toBe(4);
  });

  test('main writes through the overridden streams and error reporter', async () => {
    const out: string[] = [];
    const errors: string[] = [];

    expect(await main(['--help'], { stdout: (text) => out.push(text) })).toBe(0);
    expect(out.join('')).toContain('--config');

    const failure = await main(['--config', join(root, 'missing.json')], { error: (message) => errors.push(message) });
    expect(failure).toBe(1);
    expect(errors).toHaveLength(1);
  });
});

describe('main with the default worker', () => {
  test('uses the overridden GitHub, Git, lock guard and stop request, then releases the signal listeners', async () => {
    const signals = new FakeSignals();
    const calls: string[] = [];
    const errors: string[] = [];
    const exit = await main(['--config', config, '--pr', PR], {
      error: (message) => errors.push(message),
      fileLockGuard: () => calls.push('lock'),
      git: {
        validate: (_path: string, repo: string) => {
          calls.push(`validate ${repo}`);
          return Promise.reject(new Blocked('origin mismatch'));
        },
      } as unknown as WorkflowGit,
      github: githubDouble({
        login: () => {
          calls.push('login');
          return Promise.resolve('bot');
        },
      }),
      stop: new StopRequest({ signals }),
    });

    expect(exit).toBe(1);
    expect(errors).toEqual(['origin mismatch']);
    expect(calls).toEqual(['lock', 'login', 'validate a/b']);
    expect(signals.count()).toBe(0);
  });
});

describe('main with the default discovery', () => {
  test('scans through the overridden GitHub until the stop request, then releases the signal listeners', async () => {
    const signals = new FakeSignals();
    const calls: string[] = [];
    const exit = await main(['--config', config], {
      fileLockGuard: () => calls.push('lock'),
      github: githubDouble({
        openPrs: (repo, author) => {
          calls.push(`openPrs ${repo} ${author}`);
          signals.emit('SIGINT');
          return Promise.resolve([]);
        },
      }),
      stop: new StopRequest({ exit: () => undefined, signals }),
    });

    expect(exit).toBe(0);
    expect(calls).toEqual(['lock', 'openPrs a/b bot']);
    expect(signals.count()).toBe(0);
  });
});
