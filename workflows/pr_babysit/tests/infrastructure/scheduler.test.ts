import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { ProcessHandle, ProcessOptions, ProcessResult } from '../../../../src/loop/index.ts';
import type { ActionsRun, GithubService, PullRequest, Snapshot } from '../../infrastructure/github.ts';

import { ProcessFailedError, processRunner } from '../../../../src/loop/index.ts';
import { remoteFingerprint } from '../../infrastructure/github.ts';
import { childCommand, childEnvironment, serve, WORKER_ENTRY } from '../../infrastructure/scheduler.ts';
import { Blocked, processLock, PrState, Registry, schedulerLockPath } from '../../infrastructure/state.ts';
import { Repository, Settings } from '../../settings.ts';
import { NO_OP } from '../support/remote.ts';

const pr = (number: number): PullRequest => ({
  draft: false,
  head: { ref: 'feature', sha: `sha${String(number)}` },
  html_url: `https://github.com/a/b/pull/${String(number)}`,
  number,
  state: 'open',
  user: { login: 'me' },
});

/** A worker child that exits when told to. */
class FakeChild implements ProcessHandle {
  readonly pid: number;
  terminated = false;
  get signal(): null | string {
    return this.#signal;
  }
  #code: null | number = null;
  readonly #settled: Promise<ProcessResult>;
  #signal: null | string = null;

  constructor(
    readonly argv: readonly string[],
    readonly options: ProcessOptions,
    pid: number,
  ) {
    this.pid = pid;
    this.#settled = new Promise((resolve) => {
      this.#resolve = resolve;
    });
  }

  exit(code: number, signal: null | string = null, stderr = ''): void {
    this.#code = code;
    this.#signal = signal;
    this.#resolve({ argv: this.argv, exitCode: code, signal, stderr, stdout: '' });
  }

  poll(): null | number {
    return this.#code;
  }

  terminate(): void {
    this.terminated = true;
    this.exit(143, 'SIGTERM');
  }

  wait(): Promise<ProcessResult> {
    return this.#settled;
  }

  #resolve: (result: ProcessResult) => void = () => undefined;
}

/** An in-memory GitHub with a movable set of open PRs. */
class FakeGithub implements GithubService {
  readonly authors: string[] = [];
  closed = false;
  failures: Error[] = [];
  prs: PullRequest[] = [pr(1)];
  sha = 'sha1';

  login(): Promise<string> {
    return Promise.resolve('me');
  }

  logs(): Promise<string> {
    return Promise.resolve('');
  }

  openPrs(_repo: string, author: string): Promise<PullRequest[]> {
    this.authors.push(author);
    const failure = this.failures.shift();
    return failure === undefined ? Promise.resolve(this.prs) : Promise.reject(failure);
  }

  pullRequest(): Promise<PullRequest> {
    return Promise.reject(new Error('unused'));
  }

  reply(): Promise<void> {
    return Promise.resolve();
  }

  requestCopilot(): Promise<void> {
    return Promise.resolve();
  }

  retry(): Promise<void> {
    return Promise.resolve();
  }

  snapshot(): Promise<Snapshot> {
    const runs: ActionsRun[] = [];
    return Promise.resolve({
      checks: [],
      other: [],
      pr: { ...pr(1), head: { ref: 'feature', sha: this.sha }, state: this.closed ? 'closed' : 'open' },
      runs,
      statuses: [],
      threads: [],
    });
  }
}

let root: string;
let settings: Settings;

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'pr-babysit-scheduler-')));
  settings = new Settings({ repositories: [new Repository('a/b', root, NO_OP, NO_OP)], stateDir: join(root, 'state') });
});

afterEach(() => {
  rmSync(root, { force: true, recursive: true });
});

const config = (): string => join(root, 'config.json');

interface Harness {
  children: FakeChild[];
  github: FakeGithub;
  /** Runs the scheduler for `ticks` discovery scans, calling `between` after each. */
  run: (options?: { includeExisting?: boolean; retry?: string }, between?: (tick: number) => void) => Promise<number>;
}

function harness(ticks: number, overrides: Settings = settings): Harness {
  const github = new FakeGithub();
  const children: FakeChild[] = [];
  return {
    children,
    github,
    run: (options = {}, between = () => undefined) => {
      let tick = 0;
      return serve(overrides, config(), options, {
        github,
        spawn: (argv, spawnOptions) => {
          const child = new FakeChild(argv, spawnOptions, 1000 + children.length);
          children.push(child);
          return child;
        },
        stop: () => tick >= ticks,
        wait: () => {
          tick += 1;
          between(tick);
          return Promise.resolve();
        },
      });
    },
  };
}

describe('discovery', () => {
  test('the first scan only baselines; later PRs queue', () => {
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([['a/b', pr(1)]], false);
    expect(new PrState(settings.stateDir, 'a/b', 1).data.status).toBe('ignored');

    const registry = new Registry(settings.stateDir, 'me', ['a/b']);
    registry.enroll([['a/b', pr(1)], ['a/b', pr(2)]], false);

    expect(new PrState(settings.stateDir, 'a/b', 2).data.status).toBe('queued');
    expect(Object.keys(registry.data.prs)).toHaveLength(2);
  });

  test('deduplicates, and starts a new PR while another still runs', async () => {
    const { children, github, run } = harness(3);

    await run({ includeExisting: true }, () => {
      github.prs = [pr(1), pr(2)];
    });

    expect(children).toHaveLength(2);
    expect(children.map((child) => child.argv.at(-1))).toEqual([
      'https://github.com/a/b/pull/1',
      'https://github.com/a/b/pull/2',
    ]);
  });

  test('resolves the author from the authenticated account unless configured', async () => {
    const first = harness(1);
    await first.run();
    expect(first.github.authors).toEqual(['me']);

    const configured = new Settings({ author: 'someone', repositories: settings.repositories, stateDir: join(root, 'other') });
    const second = harness(1, configured);
    await second.run();
    expect(second.github.authors).toEqual(['someone']);
  });

  test('a blocked PR resumes only after the remote changes', async () => {
    const { children, github, run } = harness(2);
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([['a/b', pr(1)]], true);
    new PrState(settings.stateDir, 'a/b', 1).status('blocked', { fingerprint: remoteFingerprint(await github.snapshot()) });

    await run({}, () => {
      github.sha = 'new';
    });

    expect(children).toHaveLength(1);
  });

  test('a reopened enrolled PR starts again', async () => {
    const { children, run } = harness(1);
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([['a/b', pr(1)]], true);
    new PrState(settings.stateDir, 'a/b', 1).status('completed');

    await run();

    expect(children).toHaveLength(1);
  });

  test('an enrolled PR that is no longer open completes', async () => {
    const { children, github, run } = harness(1);
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([['a/b', pr(1)]], true);
    github.prs = [];
    github.closed = true;

    await run();

    expect(children).toHaveLength(0);
    expect(new PrState(settings.stateDir, 'a/b', 1).data.status).toBe('completed');
  });

  test('a failed scan is skipped and the next one proceeds', async () => {
    const { children, github, run } = harness(2);
    github.failures = [new ProcessFailedError({ argv: ['gh'], exitCode: 1, signal: null, stderr: '', stdout: '' })];

    await run({ includeExisting: true });

    expect(children).toHaveLength(1);
  });
});

describe('workers', () => {
  test('are limited by concurrency', async () => {
    const limited = new Settings({ concurrency: 1, repositories: settings.repositories, stateDir: settings.stateDir });
    const { children, github, run } = harness(2, limited);
    github.prs.push(pr(2));

    await run({ includeExisting: true });

    expect(children).toHaveLength(1);
  });

  test('are not started for a PR whose worker lock is still held', async () => {
    const { children, run } = harness(2);
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([['a/b', pr(1)]], true);
    const held = new PrState(settings.stateDir, 'a/b', 1);

    await processLock(held.lockPath, () => run());

    expect(children).toHaveLength(0);
  });

  test('start as the current runtime executable with the config and PR URL, without inherited trace context', async () => {
    const { children, run } = harness(1);

    await run({ includeExisting: true });

    expect(children[0]?.argv).toEqual([process.execPath, WORKER_ENTRY, '--config', config(), '--pr', pr(1).html_url]);
    expect(children[0]?.argv).toEqual(childCommand(config(), pr(1).html_url));
    expect(children[0]?.options.env).toEqual(childEnvironment());
  });

  test('that exit without an outcome block the PR until an explicit retry', async () => {
    const { children, run } = harness(4);

    await run({ includeExisting: true }, (tick) => {
      if (tick === 1) children[0]?.exit(3, null, 'boom');
    });

    const state = new PrState(settings.stateDir, 'a/b', 1);
    expect(state.data.status).toBe('blocked');
    expect(state.data.fingerprint).toBe('worker-crash');
    expect(state.data.reason).toBe('Worker exited 3; explicit retry required');
    expect(children).toHaveLength(1);
  });

  test('killed by a signal are reported with Python\'s negative exit code', async () => {
    const { children, run } = harness(2);

    await run({ includeExisting: true }, () => {
      children[0]?.exit(137, 'SIGKILL');
    });

    expect(new PrState(settings.stateDir, 'a/b', 1).data.reason).toBe('Worker exited -9; explicit retry required');
  });

  test('that crashed restart only for the explicit --retry URL', async () => {
    const { children, run } = harness(5);
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([['a/b', pr(1)]], true);
    new PrState(settings.stateDir, 'a/b', 1).markWorkerCrash(1);

    await run({ retry: pr(1).html_url });

    expect(children).toHaveLength(1);
  });

  test('that finished with their own outcome are not marked as crashed', async () => {
    const { children, run } = harness(3);

    await run({ includeExisting: true }, (tick) => {
      if (tick === 1) {
        new PrState(settings.stateDir, 'a/b', 1).status('blocked', { fingerprint: 'abc', reason: 'review needed' });
        children[0]?.exit(1);
      }
    });

    const state = new PrState(settings.stateDir, 'a/b', 1);
    expect(state.data.fingerprint).toBe('abc');
    expect(state.data.reason).toBe('review needed');
  });

  test('still running are terminated when discovery stops', async () => {
    const { children, run } = harness(1);

    expect(await run({ includeExisting: true })).toBe(0);

    expect(children[0]?.terminated).toBe(true);
  });
});

describe('the scheduler lock', () => {
  test('a second scheduler for the same state directory is Blocked', async () => {
    const lock = schedulerLockPath(settings.stateDir);
    const { run } = harness(1);

    const failure = await processLock(lock, () => run().catch((error: unknown) => error));

    expect(failure).toBeInstanceOf(Blocked);
    expect((failure as Error).message).toBe(`Another process owns ${lock}`);
  });

  test('a changed discovery configuration does not silently rebaseline', () => {
    new Registry(settings.stateDir, 'me', ['a/b']).enroll([], false);

    expect(() => new Registry(settings.stateDir, 'another-user', ['a/b'])).toThrow('configuration changed');
  });
});

describe('trace context', () => {
  test('inherited trace-context variables are unset, whatever their case, and exporter settings are kept', () => {
    const overrides = childEnvironment({
      baggage: 'something',
      OTEL_EXPORTER_OTLP_ENDPOINT: 'http://localhost:4318',
      TRACEPARENT: 'parent',
      tracestate: 'state',
    });

    expect(overrides).toEqual({ baggage: undefined, TRACEPARENT: undefined, tracestate: undefined });
  });

  test('a real child started with those overrides sees no trace context but keeps the exporter settings', async () => {
    const saved = { endpoint: process.env.OTEL_EXPORTER_OTLP_ENDPOINT, parent: process.env.TRACEPARENT };
    process.env.TRACEPARENT = '00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01';
    process.env.OTEL_EXPORTER_OTLP_ENDPOINT = 'http://localhost:4318';
    try {
      const result = await processRunner.run(
        [process.execPath, '-e', 'console.log(JSON.stringify([process.env.TRACEPARENT ?? null, process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? null]))'],
        { check: true, env: childEnvironment() },
      );

      expect(JSON.parse(result.stdout)).toEqual([null, 'http://localhost:4318']);
    } finally {
      for (const [name, value] of [['TRACEPARENT', saved.parent], ['OTEL_EXPORTER_OTLP_ENDPOINT', saved.endpoint]] as const) {
        if (value === undefined) Reflect.deleteProperty(process.env, name);
        else process.env[name] = value;
      }
    }
  });
});
