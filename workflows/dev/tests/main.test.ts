import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { TelemetryUnavailableError } from '../../../src/loop/index.ts';
import { type DevDependencies, main } from '../main.ts';
import { GitHubRepo } from '../platforms.ts';
import { RecordingObserver, silenceLogging } from './support/observer.ts';
import { Stream } from './support/stream.ts';

interface Session {
  dependencies: DevDependencies;
  logDir: string;
  observers: RecordingObserver[];
  stderr: Stream;
  stdout: Stream;
}

let directory: string;

beforeEach(() => {
  directory = realpathSync(mkdtempSync(join(tmpdir(), 'dev-main-')));
});

afterEach(() => {
  silenceLogging();
  rmSync(directory, { force: true, recursive: true });
});

function session(overrides: DevDependencies = {}, stderr = new Stream()): Session {
  const observers: RecordingObserver[] = [];
  const stdout = new Stream();
  return {
    dependencies: {
      clock: () => 0,
      cwd: () => directory,
      env: {},
      newObserver: (endpoint) => {
        const observer = new RecordingObserver(endpoint);
        observers.push(observer);
        return observer;
      },
      stderr,
      stdout,
      ...overrides,
    },
    logDir: join(directory, '.loop'),
    observers,
    stderr,
    stdout,
  };
}

const lines = (text: string): string[] => text.split('\n').filter((line) => line !== '');

describe('--fake', () => {
  test('fakes every external dependency and completes with a log', async () => {
    const { dependencies, logDir, stderr } = session();

    const code = await main(
      ['--fake', 'agent', '--fake', 'git', '--fake', 'github', '--fake', 'store', '--fake-speed', '0', '--log-level', 'DEBUG'],
      dependencies,
    );

    expect(code).toBe(0);
    expect(existsSync(join(logDir, 'dev.log'))).toBe(true);
    expect(readFileSync(join(logDir, 'dev.log'), 'utf8')).toContain('"message": "Spec #1 of antshc/loop was not delivered');
    expect(stderr.text).toContain('loop.workflow: Spec');
  });

  test('shows only high-level Workflow records on the console', async () => {
    const { dependencies, stderr } = session();

    await main(['--dry', '--no-otel', '--log-level', 'DEBUG', '--log-dir', join(directory, 'logs')], dependencies);

    expect(lines(stderr.text).length).toBeGreaterThan(0);
    for (const line of lines(stderr.text)) {
      expect(line).toMatch(/ (INFO|WARNING) (loop\.workflow|workflow\.dev): /);
    }
    expect(stderr.text).not.toContain('DEBUG');
  });
});

describe('--dry', () => {
  test('publishes telemetry to the default dashboard endpoint and flushes it', async () => {
    const { dependencies, observers } = session();

    const code = await main(['--dry', '--fake-speed', '0'], dependencies);

    expect(code).toBe(0);
    expect(observers.map((observer) => observer.endpoint)).toEqual(['http://localhost:4318']);
    expect(observers[0]?.shutdowns).toBe(1);
  });

  test('reads the standard endpoint variable, which an explicit endpoint overrides', async () => {
    const env = { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://env:4318' };
    const fromEnv = session({ env });
    const explicit = session({ env });

    await main(['--dry'], fromEnv.dependencies);
    await main(['--dry', '--otlp-endpoint', 'http://collector:4318'], explicit.dependencies);

    expect(fromEnv.observers.map((observer) => observer.endpoint)).toEqual(['http://env:4318']);
    expect(explicit.observers.map((observer) => observer.endpoint)).toEqual(['http://collector:4318']);
  });

  test('opens the Workflow root and Spec spans under the shared roles', async () => {
    const { dependencies, observers } = session();

    await main(['--dry'], dependencies);

    const spans = observers[0]?.spans ?? [];
    expect(spans.map((span) => [span.name, span.role])).toEqual([
      ['dev', 'code'],
      ['Spec', 'engineer'],
    ]);
    expect(spans[1]?.parent).toBe(spans[0] ?? null);
    expect(spans[1]?.attributes).toMatchObject({ 'loop.initiative': 'Basket', 'loop.spec': 1 });
  });

  test('with --no-otel builds no observer', async () => {
    const { dependencies, observers } = session({
      newObserver: () => {
        throw new Error('telemetry must be opt-in');
      },
    });

    expect(await main(['--dry', '--no-otel'], dependencies)).toBe(0);
    expect(observers).toEqual([]);
  });

  test('without an endpoint builds no observer', async () => {
    const { dependencies } = session({
      newObserver: () => {
        throw new Error('telemetry must be opt-in');
      },
    });

    expect(await main(['--fake', 'agent', '--fake', 'github', '--fake', 'git', '--fake', 'store'], dependencies)).toBe(0);
  });

  test('delivers only the requested Spec', async () => {
    const { dependencies, observers } = session();

    expect(await main(['--dry', '--spec', '1'], dependencies)).toBe(0);
    expect(observers[0]?.spans.map((span) => span.name)).toEqual(['dev', 'Spec']);
  });

  test('exits 1 and says so when the requested Spec does not exist', async () => {
    const { dependencies, stderr } = session();

    expect(await main(['--dry', '--no-otel', '--spec', '7'], dependencies)).toBe(1);
    expect(stderr.text).toContain('ERROR workflow.dev: no open Spec #7 on antshc/loop');
  });
});

describe('--visualize', () => {
  test('prints one final table without escape sequences when the output is redirected', async () => {
    const { dependencies, stderr } = session();

    expect(await main(['--dry', '--no-otel', '--visualize'], dependencies)).toBe(0);

    expect(stderr.text).not.toContain('\u001B');
    expect(stderr.text.match(/Workflow name/g)).toHaveLength(1);
    expect(stderr.text).toContain('Spec');
  });

  test('redraws the table in place on a terminal', async () => {
    const { dependencies, stderr } = session({}, new Stream(true));

    await main(['--dry', '--no-otel', '--visualize'], dependencies);

    expect(stderr.text).toContain('\u001B');
  });

  test('shows no table without the flag', async () => {
    const { dependencies, stderr } = session();

    await main(['--dry', '--no-otel'], dependencies);

    expect(stderr.text).not.toContain('Workflow name');
  });
});

describe('--log-dir and --log-level', () => {
  test('writes the debug log to the folder named', async () => {
    const { dependencies } = session();
    const custom = join(directory, 'custom-logs');

    const code = await main(['--dry', '--no-otel', '--log-dir', custom, '--log-level', 'DEBUG'], dependencies);

    expect(code).toBe(0);
    expect(existsSync(join(custom, 'dev.log'))).toBe(true);
  });

  test('defaults the folder to .loop under the working directory and writes no file above debug', async () => {
    const { dependencies } = session();

    await main(['--dry', '--no-otel'], dependencies);

    expect(existsSync(join(directory, '.loop', 'dev.log'))).toBe(false);
  });
});

describe('failure', () => {
  test('an unexpected error is logged and the run exits 1', async () => {
    const { dependencies, logDir, stderr } = session({
      githubFactory: () => new GitHubRepo('o', 'r', () => Promise.reject(new Error('boom'))),
    });

    const code = await main(['--fake', 'agent', '--fake', 'git', '--fake', 'store', '--log-level', 'DEBUG'], dependencies);

    expect(code).toBe(1);
    expect(stderr.text).toContain('ERROR workflow.dev: dev failed (Error); enable DEBUG for details');
    expect(readFileSync(join(logDir, 'dev.log'), 'utf8')).toContain('boom');
  });

  test('unavailable telemetry is reported and the run exits 1', async () => {
    const { dependencies, stderr } = session({
      newObserver: () => Promise.reject(new TelemetryUnavailableError()),
    });

    expect(await main(['--dry'], dependencies)).toBe(1);
    expect(stderr.text).toContain('ERROR workflow.dev: dev unavailable: OTLP telemetry export needs');
  });

  test('flushes the observer even when the run fails', async () => {
    const { dependencies, observers } = session({
      githubFactory: () => new GitHubRepo('o', 'r', () => Promise.reject(new Error('boom'))),
    });

    expect(await main(['--fake', 'agent', '--fake', 'git', '--fake', 'store', '--otlp-endpoint', 'http://x:1'], dependencies)).toBe(1);
    expect(observers[0]?.shutdowns).toBe(1);
  });
});

describe('bad input', () => {
  test.each([
    ['an unknown flag', ['--nope']],
    ['an unknown seam', ['--fake', 'docker']],
    ['a non-integer --spec', ['--spec', 'x']],
    ['a faked git without a faked agent', ['--fake', 'git']],
  ])(
    'prints usage, exits 2 and starts nothing for %s',
    async (_name, argv) => {
      const { dependencies, logDir, observers, stderr } = session();

      expect(await main(argv, dependencies)).toBe(2);

      expect(stderr.text).toContain('Usage: dev [options]');
      expect(existsSync(logDir)).toBe(false);
      expect(observers).toEqual([]);
    },
  );

  test('--help prints the help to stdout and exits 0', async () => {
    const { dependencies, stderr, stdout } = session();

    expect(await main(['--help'], dependencies)).toBe(0);

    expect(stdout.text).toContain('Usage: dev [options]');
    expect(stderr.text).toBe('');
  });
});
