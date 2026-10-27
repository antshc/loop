import { describe, expect, test } from 'bun:test';
import { join } from 'node:path';

import type { OutputStream, SpanObserver } from '../../src/loop/index.ts';

import { compose, main, TOKENS, type WorkflowReportingDependencies } from '../../workflows/workflow_reporting.ts';
import { SpanRecorder } from './support/span-observer.ts';

interface Session {
  dependencies: WorkflowReportingDependencies;
  stderr: Stream;
  stdout: Stream;
}

class Stream implements OutputStream {
  readonly writes: string[] = [];

  get text(): string {
    return this.writes.join('');
  }

  constructor(readonly isTTY = false) {}

  write(text: string): void {
    this.writes.push(text);
  }
}

function session(overrides: WorkflowReportingDependencies = {}): Session {
  const stderr = new Stream();
  const stdout = new Stream();
  return {
    dependencies: { clock: () => 0, now: () => new Date(2026, 0, 2, 3, 4, 5, 6), stderr, stdout, ...overrides },
    stderr,
    stdout,
  };
}

const STAMP = '2026-01-02 03:04:05,006 ';

/** Console records without their time stamp and varying duration. */
const records = (text: string): string[] =>
  text
    .split('\n')
    .filter((line) => line.startsWith(STAMP))
    .map((line) => line.slice(STAMP.length));

describe('the fake e-commerce delivery', () => {
  test('recovers a retried ticket and exits 0', async () => {
    const { dependencies, stderr } = session();

    expect(await main([], dependencies)).toBe(0);

    expect(records(stderr.text)).toEqual([
      'INFO loop.workflow: eShop development · spec #42 started',
      'INFO loop.workflow: Prepare repository · spec #42 started',
      'INFO loop.workflow: Prepare repository · spec #42 completed · 0.0s',
      'INFO loop.workflow: Implement ticket · spec #42 · ticket #43 · attempt 1 started',
      'WARNING loop.workflow: Implement ticket · spec #42 · ticket #43 · attempt 1 failed · 0.0s · No changes produced',
      'INFO loop.workflow: Implement ticket · spec #42 · ticket #43 · attempt 2 started',
      'INFO loop.workflow: Implement ticket · spec #42 · ticket #43 · attempt 2 completed · 0.0s',
      'INFO loop.workflow: Implement ticket · spec #42 · ticket #44 · attempt 1 started',
      'INFO loop.workflow: Implement ticket · spec #42 · ticket #44 · attempt 1 completed · 0.0s',
      'INFO loop.workflow: Publish draft PR · spec #42 started',
      'INFO loop.workflow: Publish draft PR · spec #42 completed · 0.0s · pull_request=fake://eshop/pull/57',
      'INFO loop.workflow: Verify application · spec #42 started',
      'INFO loop.workflow: Verify application · spec #42 completed · 0.0s · verdict=pass',
      'INFO loop.workflow: Refine changes · spec #42 started',
      'INFO loop.workflow: Refine changes · spec #42 skipped · 0.0s · No cleanup configured',
      'INFO loop.workflow: Review pull request · spec #42 started',
      'INFO loop.workflow: Review pull request · spec #42 completed · 0.0s · blocking_findings=0',
      'INFO loop.workflow: Check readiness · spec #42 started',
      'INFO loop.workflow: Check readiness · spec #42 completed · 0.0s · ready=True',
      'INFO loop.workflow: eShop development · spec #42 completed · 0.0s · verdict=pass · ready=True',
    ]);
  });

  test('a failed verification skips review and finalization, keeps the PR a draft and exits 1', async () => {
    const { dependencies, stderr } = session();

    expect(await main(['--fail-verification'], dependencies)).toBe(1);

    const lines = records(stderr.text);
    expect(lines).toContain('INFO loop.workflow: Review pull request · spec #42 skipped · 0.0s · Verification failed');
    expect(lines).toContain('INFO loop.workflow: Check readiness · spec #42 skipped · 0.0s · PR remains a draft');
    expect(lines).toContain(
      'WARNING loop.workflow: Verify application · spec #42 failed · 0.0s · verdict=fail · Basket verification failed',
    );
    expect(lines.at(-1)).toBe(
      'WARNING loop.workflow: eShop development · spec #42 failed · 0.0s · verdict=fail · ready=False · Application verification failed; PR remains a draft',
    );
  });

  test('--visualize prints the phase table once to redirected stderr', async () => {
    const { dependencies, stderr } = session();

    expect(await main(['--visualize'], dependencies)).toBe(0);

    const table = stderr.text.slice(stderr.text.indexOf('┏'));
    expect(table).toBe(
      [
        '┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━┓',
        '┃ Workflow name     ┃ Phase                                                ┃ Owner     ┃ Status    ┃ Time ┃',
        '┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━┩',
        '│ eShop development │ eShop development · spec #42                         │ code      │ completed │ 0.0s │',
        '│ eShop development │ Prepare repository · spec #42                        │ code      │ completed │ 0.0s │',
        '│ eShop development │ Implement ticket · spec #42 · ticket #43 · attempt 1 │ developer │ failed    │ 0.0s │',
        '│ eShop development │ Implement ticket · spec #42 · ticket #43 · attempt 2 │ developer │ completed │ 0.0s │',
        '│ eShop development │ Implement ticket · spec #42 · ticket #44 · attempt 1 │ developer │ completed │ 0.0s │',
        '│ eShop development │ Publish draft PR · spec #42                          │ code      │ completed │ 0.0s │',
        '│ eShop development │ Verify application · spec #42                        │ verifier  │ completed │ 0.0s │',
        '│ eShop development │ Refine changes · spec #42                            │ chore     │ skipped   │ 0.0s │',
        '│ eShop development │ Review pull request · spec #42                       │ reviewer  │ completed │ 0.0s │',
        '│ eShop development │ Check readiness · spec #42                           │ code      │ completed │ 0.0s │',
        '└───────────────────┴──────────────────────────────────────────────────────┴───────────┴───────────┴──────┘',
        '',
      ].join('\n'),
    );
  });

  test('without --visualize no table is printed', async () => {
    const { dependencies, stderr } = session();

    await main([], dependencies);

    expect(stderr.text).not.toContain('┏');
  });

  test('--otel-endpoint builds the observer for that endpoint and shares it with phase reporting', async () => {
    const observer = new SpanRecorder();
    const endpoints: string[] = [];
    const otlpObserver = (endpoint: string): SpanObserver => {
      endpoints.push(endpoint);
      return observer;
    };
    const { dependencies } = session({ otlpObserver });

    expect(await main(['--otel-endpoint', 'http://collector:4318'], dependencies)).toBe(0);

    expect(endpoints).toEqual(['http://collector:4318']);
    const names = observer.spans.map((span) => span.name);
    expect(names).toContain('eShop development');
    expect(names).toContain('Implement ticket');
    expect(await main([], dependencies)).toBe(0);
    expect(endpoints).toHaveLength(1);
  });

  test('missing dependencies fall back to the shipped implementations', async () => {
    const { stderr, stdout } = session();

    expect(await main([], { stderr, stdout })).toBe(0);

    expect(stderr.text).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO loop\.workflow: eShop development · spec #42 started\n/);
  });
});

describe('the command line', () => {
  test('bad input prints usage on stderr and returns 2 without running', async () => {
    const { dependencies, stderr, stdout } = session();

    expect(await main(['--nope'], dependencies)).toBe(2);
    expect(await main(['extra'], dependencies)).toBe(2);
    expect(await main(['--otel-endpoint'], dependencies)).toBe(2);

    expect(stderr.text).toContain('Usage: workflow_reporting [options]');
    expect(records(stderr.text)).toEqual([]);
    expect(stdout.text).toBe('');
  });

  test('help goes to stdout and returns 0', async () => {
    const { dependencies, stderr, stdout } = session();

    expect(await main(['--help'], dependencies)).toBe(0);

    expect(stdout.text).toContain('A fake e-commerce delivery that demonstrates optional phase reporting.');
    for (const option of ['--visualize', '--fail-verification', '--otel-endpoint <endpoint>']) {
      expect(stdout.text).toContain(option);
    }
    expect(stderr.text).toBe('');
  });
});

describe('running as a script', () => {
  const script = join(import.meta.dir, '..', '..', 'workflows', 'workflow_reporting.ts');
  const run = async (...args: string[]): Promise<{ code: number; stderr: string }> => {
    const child = Bun.spawn([process.execPath, script, ...args], { stderr: 'pipe', stdout: 'pipe' });
    const [stderr, code] = await Promise.all([new Response(child.stderr).text(), child.exited]);
    return { code, stderr };
  };

  test('the exit code reaches the process exit status', async () => {
    expect((await run()).code).toBe(0);
    expect((await run('--fail-verification')).code).toBe(1);
  });

  test('bad input exits with status 2 and usage', async () => {
    const result = await run('--nope');

    expect(result.code).toBe(2);
    expect(result.stderr).toContain('Usage: workflow_reporting [options]');
  });
});

describe('the composition root', () => {
  test('binds every seam with the shipped implementation', () => {
    const container = compose();

    expect(container.get<() => number>(TOKENS.clock)()).toBeGreaterThanOrEqual(0);
    expect(container.get<() => Date>(TOKENS.now)()).toBeInstanceOf(Date);
    expect(container.get<unknown>(TOKENS.stderr)).toBe(process.stderr);
    expect(container.get<unknown>(TOKENS.stdout)).toBe(process.stdout);
    expect(typeof container.get<unknown>(TOKENS.otlpObserver)).toBe('function');
  });

  test('shares one instance of each service', () => {
    const container = compose();

    expect(container.get<unknown>(TOKENS.workflow)).toBe(container.get<unknown>(TOKENS.workflow));
    expect(container.get<unknown>(TOKENS.commandLine)).toBe(container.get<unknown>(TOKENS.commandLine));
  });

  test('each override rebinds the token of the same name', () => {
    const { dependencies } = session({ otlpObserver: () => new SpanRecorder() });
    const container = compose(dependencies);

    expect(container.get<unknown>(TOKENS.clock)).toBe(dependencies.clock);
    expect(container.get<unknown>(TOKENS.now)).toBe(dependencies.now);
    expect(container.get<unknown>(TOKENS.otlpObserver)).toBe(dependencies.otlpObserver);
    expect(container.get<unknown>(TOKENS.stderr)).toBe(dependencies.stderr);
    expect(container.get<unknown>(TOKENS.stdout)).toBe(dependencies.stdout);
  });

  test('a missing or undefined override keeps the shipped implementation', () => {
    const container = compose({ clock: undefined, now: () => new Date(0) });

    expect(container.get<() => Date>(TOKENS.now)().getTime()).toBe(0);
    expect(container.get<unknown>(TOKENS.stderr)).toBe(process.stderr);
    expect(container.get<() => number>(TOKENS.clock)()).toBeGreaterThan(0);
  });

  test('the stream overrides reach the command line and the console', async () => {
    const stderr = new Stream();
    const stdout = new Stream();
    const dependencies = { clock: () => 0, now: () => new Date(2026, 0, 2, 3, 4, 5, 6), stderr, stdout };

    expect(await main(['--help'], dependencies)).toBe(0);
    expect(stdout.text).toContain('Usage: workflow_reporting');
    expect(await main([], dependencies)).toBe(0);
    expect(records(stderr.text)[0]).toBe('INFO loop.workflow: eShop development · spec #42 started');
  });
});
