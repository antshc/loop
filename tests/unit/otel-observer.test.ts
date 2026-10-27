import { SpanStatusCode } from '@opentelemetry/api';
import {
  AggregationTemporality,
  DataPointType,
  InMemoryMetricExporter,
  MeterProvider,
  PeriodicExportingMetricReader,
} from '@opentelemetry/sdk-metrics';
import {
  BasicTracerProvider,
  InMemorySpanExporter,
  type ReadableSpan,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { afterEach, describe, expect, test } from 'bun:test';

import type { LogRecord } from '../../src/loop/index.ts';

import { setLogSink } from '../../src/loop/diagnostics.ts';
import {
  AgentRunRecord,
  type ModuleLoader,
  type OtlpObserver,
  otlpObserver,
  type OtlpObserverOptions,
  type PhaseSpan,
  type PhaseSpanEnd,
  TelemetryUnavailableError,
  ToolCall,
  Usage,
  WorktreeEvent,
} from '../../src/loop/index.ts';
import { hrTime, TELEMETRY_PACKAGES } from '../../src/loop/observe/otel.ts';
import { golden } from './support/fixtures.ts';

interface Contract {
  metrics: unknown[];
  spans: unknown[];
}

interface Scenarios {
  failed: Contract;
  full: Contract;
}

const START = new Date('2026-10-23T13:00:00Z');
const CWD = '/work/tree';
const STATUSES = ['UNSET', 'OK', 'ERROR'];
const KINDS = ['INTERNAL', 'SERVER', 'CLIENT', 'PRODUCER', 'CONSUMER'];
const LIBRARY_SPANS = ['execute_tool', 'invoke_agent', 'loop.worktree'];
const REPOSITORY = new URL('../..', import.meta.url).pathname;

const USAGE = new Usage({
  aiu: { input: 0.1, output: 0.1 },
  apiCalls: 2,
  cacheReadTokens: 2100,
  cacheWriteTokens: 400,
  inputTokens: 500,
  outputTokens: 100,
  premiumRequests: 1,
  reasoningTokens: 10,
});

const DONE: PhaseSpanEnd = { attributes: {}, error: null, gates: [] };

/** The observer over OpenTelemetry's in-memory exporters. */
class Capture {
  readonly meters = new InMemoryMetricExporter(AggregationTemporality.CUMULATIVE);
  readonly spans = new InMemorySpanExporter();
  get observer(): OtlpObserver {
    if (this.#observer === null) {
      throw new Error('call start() first');
    }
    return this.#observer;
  }
  #observer: null | OtlpObserver = null;

  readonly #provider = new MeterProvider({
    readers: [new PeriodicExportingMetricReader({ exporter: this.meters, exportIntervalMillis: 60_000 })],
  });

  static async start(options: OtlpObserverOptions = {}): Promise<Capture> {
    const capture = new Capture();
    capture.#observer = await otlpObserver({
      captureContent: true,
      meterProvider: capture.#provider,
      tracerProvider: new BasicTracerProvider({ spanProcessors: [new SimpleSpanProcessor(capture.spans)] }),
      ...options,
    });
    return capture;
  }

  async contract(): Promise<Contract> {
    await this.#provider.forceFlush();
    return { metrics: this.#metrics(), spans: spansOf(this.spans) };
  }

  span(name: string): ReadableSpan {
    const found = this.spans.getFinishedSpans().filter((span) => span.name === name);
    expect(found.map((span) => span.name)).toEqual([name]);
    const [first] = found;
    if (first === undefined) {
      throw new Error(`no span named ${name}`);
    }
    return first;
  }

  #metrics(): unknown[] {
    return this.meters
      .getMetrics()
      .flatMap((resource) => resource.scopeMetrics)
      .flatMap((scope) => scope.metrics)
      .map((metric) => {
        const histogram = metric.dataPointType === DataPointType.HISTOGRAM;
        const points = metric.dataPoints
          .map((point) => ({
            attributes: sorted(point.attributes),
            ...(histogram
              ? { count: (point.value as { count: number }).count, sum: (point.value as { sum: number }).sum }
              : { value: point.value }),
          }))
          .sort((a, b) => compare(JSON.stringify(a.attributes), JSON.stringify(b.attributes)));
        return {
          description: metric.descriptor.description,
          kind: histogram ? 'histogram' : 'counter',
          name: metric.descriptor.name,
          points,
          unit: metric.descriptor.unit,
        };
      })
      .sort((a, b) => compare(a.name, b.name));
  }
}

function at(seconds: number): Date {
  return new Date(START.getTime() + seconds * 1000);
}

function nanos(time: [number, number]): bigint {
  return BigInt(time[0]) * 1_000_000_000n + BigInt(time[1]);
}

function record(
  options: { agent?: null | string; error?: null | string; exitCode?: null | number; usage?: null | Usage } = {},
  cwd = CWD,
): AgentRunRecord {
  const usage = options.usage === undefined ? USAGE : options.usage;
  return new AgentRunRecord({
    agent: options.agent ?? null,
    cli: 'copilot',
    cwd,
    endedAt: at(5),
    error: options.error ?? null,
    exitCode: options.exitCode === undefined ? 0 : options.exitCode,
    model: 'claude-haiku-4.5',
    nativeId: 'n1',
    output: 'secret output',
    prompt: 'secret prompt',
    reasoningEffort: 'medium',
    session: 'ticket-7',
    startedAt: START,
    tools:
      usage === null
        ? []
        : [
            new ToolCall({
              arguments: { command: 'ls' },
              callId: 't1',
              endedAt: at(2),
              name: 'bash',
              ok: true,
              result: 'a.py',
              startedAt: at(1),
            }),
          ],
    usage,
  });
}

function worktree(point: 'ready' | 'removed' | 'removing' | 'retained', seconds: number, ok: boolean | null) {
  return new WorktreeEvent({ at: at(seconds), ok, path: CWD, point });
}

const logged: LogRecord[] = [];

afterEach(() => {
  setLogSink(null);
  logged.length = 0;
});

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function sorted(attributes: object): Record<string, unknown> {
  return Object.fromEntries(Object.entries(attributes).sort(([a], [b]) => compare(a, b)));
}

function spansOf(exporter: InMemorySpanExporter): unknown[] {
  const finished = exporter.getFinishedSpans();
  const names = new Map(finished.map((span) => [span.spanContext().spanId, span.name]));
  const origin = nanos([START.getTime() / 1000, 0]);
  const relative = (span: ReadableSpan, time: [number, number]): null | number =>
    LIBRARY_SPANS.some((prefix) => span.name.startsWith(prefix)) ? Number(nanos(time) - origin) / 1e9 : null;
  return [...finished]
    .sort((a, b) => Number(nanos(a.startTime) - nanos(b.startTime)) || compare(a.name, b.name))
    .map((span) => ({
      attributes: sorted(span.attributes),
      endSeconds: relative(span, span.endTime),
      events: span.events.map((event) => ({ attributes: sorted(event.attributes ?? {}), name: event.name })),
      kind: KINDS[span.kind],
      name: span.name,
      parent: span.parentSpanContext === undefined ? null : (names.get(span.parentSpanContext.spanId) ?? null),
      startSeconds: relative(span, span.startTime),
      status: STATUSES[span.status.code],
      statusDescription: span.status.message ?? null,
    }));
}

describe('span and metric contract', () => {
  test('a ticket span, worktree, agent run and tool export the names and attributes Python exported', async () => {
    const expected = await golden<Scenarios>('otel-contract.json');
    const capture = await Capture.start();

    const ticket = capture.observer.span('#1 add basket', { 'loop.ticket.number': 1 }, 'code');
    await ticket.run(async () => {
      capture.observer.worktree(worktree('ready', 0, null));
      capture.observer.agentRun(record());
      capture.observer.worktree(worktree('removing', 6, true));
      capture.observer.worktree(worktree('removed', 7, true));
      await Promise.resolve();
    });
    ticket.end(DONE);

    expect(await capture.contract()).toEqual(expected.full);
  });

  test('a failed unnamed-worktree run is marked as an error and a retained worktree says so', async () => {
    const expected = await golden<Scenarios>('otel-contract.json');
    const capture = await Capture.start();

    capture.observer.worktree(worktree('ready', 0, null));
    capture.observer.agentRun(record({ agent: 'planner', error: 'CalledProcessError(2)', exitCode: null, usage: null }));
    capture.observer.worktree(worktree('retained', 7, false));

    expect(await capture.contract()).toEqual(expected.failed);
  });

  test('prompts, outputs and tool arguments are exported only on request', async () => {
    const quiet = await Capture.start({ captureContent: false });

    quiet.observer.agentRun(record());

    expect(quiet.span('invoke_agent copilot').attributes).not.toHaveProperty(['gen_ai.input.messages']);
    expect(quiet.span('invoke_agent copilot').attributes).not.toHaveProperty(['gen_ai.output.messages']);
    expect(quiet.span('execute_tool bash').attributes).not.toHaveProperty(['gen_ai.tool.call.arguments']);
  });

  test('an unobservable run exports a span without usage and records its duration only', async () => {
    const capture = await Capture.start();

    capture.observer.agentRun(record({ usage: null }));

    expect(capture.span('invoke_agent copilot').attributes).not.toHaveProperty(['gen_ai.usage.input_tokens']);
    const metrics = (await capture.contract()).metrics as { name: string; points: { sum: number }[] }[];
    expect(metrics.map((metric) => metric.name)).toEqual(['gen_ai.client.operation.duration']);
    expect(metrics[0]?.points[0]?.sum).toBe(5);
  });

  test('epoch timestamps become the SDK time type', () => {
    expect(hrTime(new Date('2026-10-23T13:00:00.123Z'))).toEqual([Date.UTC(2026, 9, 23, 13) / 1000, 123_000_000]);
    expect(hrTime(new Date(0))).toEqual([0, 0]);
  });
});

describe('nesting', () => {
  test('without a phase, the agent run nests under its worktree', async () => {
    const capture = await Capture.start();

    capture.observer.worktree(worktree('ready', 0, null));
    capture.observer.agentRun(record());
    capture.observer.worktree(worktree('removed', 7, true));

    expect(capture.span('invoke_agent copilot').parentSpanContext?.spanId).toBe(capture.span('loop.worktree').spanContext().spanId);
    expect(capture.span('loop.worktree').status.code).toBe(SpanStatusCode.UNSET);
  });

  test('a phase takes precedence over the worktree and survives awaits', async () => {
    const capture = await Capture.start();
    const phase = capture.observer.span('#1 add basket', {}, 'code');

    await phase.run(async () => {
      capture.observer.worktree(worktree('ready', 0, null));
      await new Promise((resolve) => setTimeout(resolve, 5));
      capture.observer.agentRun(record());
      capture.observer.worktree(worktree('removed', 7, true));
    });
    phase.end(DONE);

    const ticket = capture.span('#1 add basket').spanContext().spanId;
    expect(capture.span('invoke_agent copilot').parentSpanContext?.spanId).toBe(ticket);
    expect(capture.span('loop.worktree').parentSpanContext?.spanId).toBe(ticket);
  });

  test('nested phases form a chain and concurrent phases stay apart', async () => {
    const capture = await Capture.start();
    const spec = capture.observer.span('spec', {}, 'code');

    await spec.run(async () => {
      const run = async (name: string, cwd: string): Promise<void> => {
        const ticket = capture.observer.span(name, {}, 'code');
        await ticket.run(async () => {
          await new Promise((resolve) => setTimeout(resolve, name === 'ticket a' ? 10 : 1));
          capture.observer.agentRun(record({ agent: name }, cwd));
        });
        ticket.end(DONE);
      };
      await Promise.all([run('ticket a', '/a'), run('ticket b', '/b')]);
    });
    spec.end(DONE);

    const id = (name: string): string => capture.span(name).spanContext().spanId;
    expect(capture.span('ticket a').parentSpanContext?.spanId).toBe(id('spec'));
    expect(capture.span('ticket b').parentSpanContext?.spanId).toBe(id('spec'));
    expect(capture.span('invoke_agent ticket a').parentSpanContext?.spanId).toBe(id('ticket a'));
    expect(capture.span('invoke_agent ticket b').parentSpanContext?.spanId).toBe(id('ticket b'));
  });

  test('a run started outside any phase begins a fresh trace', async () => {
    const capture = await Capture.start();
    const phase = capture.observer.span('first', {}, 'code');
    await phase.run(() => Promise.resolve());
    phase.end(DONE);

    capture.observer.agentRun(record());

    expect(capture.span('invoke_agent copilot').parentSpanContext).toBeUndefined();
    expect(capture.span('invoke_agent copilot').spanContext().traceId).not.toBe(capture.span('first').spanContext().traceId);
  });
});

describe('phase spans', () => {
  test('the final facts become attributes, gate events and an error status', async () => {
    const capture = await Capture.start();
    const phase = capture.observer.span('#1 add basket', { 'loop.phase.id': 'p1' }, 'code');

    phase.end({
      attributes: { 'loop.phase.status': 'failed' },
      error: 'gate failed',
      gates: [{ check: 'tests', note: '2 failing', ok: false }],
    });

    const span = capture.span('#1 add basket');
    expect(span.attributes).toEqual({ 'loop.phase.id': 'p1', 'loop.phase.status': 'failed' });
    expect(span.events.map((event) => ({ attributes: event.attributes, name: event.name }))).toEqual([
      { attributes: { 'loop.gate.check': 'tests', 'loop.gate.note': '2 failing', 'loop.gate.ok': false }, name: 'loop.gate' },
    ]);
    expect(span.status).toEqual({ code: SpanStatusCode.ERROR, message: 'gate failed' });
  });

  test('ending twice exports one span', async () => {
    const capture = await Capture.start();
    const phase: PhaseSpan = capture.observer.span('once', {}, 'code');

    phase.end(DONE);
    phase.end(DONE);

    expect(capture.spans.getFinishedSpans()).toHaveLength(1);
  });
});

describe('resources per agent', () => {
  async function perAgent(): Promise<{ capture: Capture; exporters: Map<string, InMemorySpanExporter> }> {
    const exporters = new Map<string, InMemorySpanExporter>();
    const capture = await Capture.start({
      resourceProviders: (name) => {
        const exporter = exporters.get(name) ?? new InMemorySpanExporter();
        exporters.set(name, exporter);
        return new BasicTracerProvider({ spanProcessors: [new SimpleSpanProcessor(exporter)] });
      },
    });
    return { capture, exporters };
  }

  const names = (exporter: InMemorySpanExporter | undefined): string[] =>
    (exporter?.getFinishedSpans() ?? []).map((span) => span.name).sort();

  test('named agents, Workflow roles and the worktree export as their own resources', async () => {
    const { capture, exporters } = await perAgent();
    const ticket = capture.observer.span('#1 add basket', {}, 'engineer');

    await ticket.run(() => {
      capture.observer.worktree(worktree('ready', 0, null));
      capture.observer.agentRun(record({ agent: 'planner' }));
      capture.observer.agentRun(record({ agent: 'developer' }));
      capture.observer.worktree(worktree('removed', 7, true));
      return Promise.resolve();
    });
    ticket.end(DONE);

    expect(names(exporters.get('planner'))).toEqual(['execute_tool bash', 'invoke_agent planner']);
    expect(names(exporters.get('developer'))).toEqual(['execute_tool bash', 'invoke_agent developer']);
    expect(names(exporters.get('code'))).toEqual(['loop.worktree']);
    expect(names(exporters.get('engineer'))).toEqual(['#1 add basket']);
    expect(capture.spans.getFinishedSpans()).toEqual([]);
  });

  test('an unnamed agent stays on the shared resource', async () => {
    const { capture, exporters } = await perAgent();

    capture.observer.agentRun(record());

    expect(capture.spans.getFinishedSpans().map((span) => span.name).sort()).toEqual(['execute_tool bash', 'invoke_agent copilot']);
    expect(exporters.size).toBe(0);
  });

  test('the agent name is an attribute of its span', async () => {
    const { capture, exporters } = await perAgent();

    capture.observer.agentRun(record({ agent: 'planner' }));

    const span = exporters.get('planner')?.getFinishedSpans().find((found) => found.name === 'invoke_agent planner');
    expect(span?.attributes['gen_ai.agent.name']).toBe('planner');
    expect(capture).toBeDefined();
  });
});

describe('optional packages', () => {
  const notInstalled: ModuleLoader = (specifier) => Promise.reject(new Error(`Cannot find package '${specifier}'`));

  test('requesting OTLP export without the packages fails startup with an actionable message', async () => {
    const error = await otlpObserver({ endpoint: 'http://localhost:4318', loadModule: notInstalled }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(TelemetryUnavailableError);
    const message = (error as TelemetryUnavailableError).message;
    expect(message).toContain('bun add');
    for (const name of TELEMETRY_PACKAGES) {
      expect(message).toContain(name);
    }
    expect(((error as TelemetryUnavailableError).cause as Error).message).toContain('@opentelemetry/api');
  });

  test('a partially installed set fails the same way', async () => {
    const loader: ModuleLoader = async (specifier) =>
      specifier.includes('exporter') ? notInstalled(specifier) : import(specifier);

    const error = await otlpObserver({ endpoint: 'http://localhost:4318', loadModule: loader }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(TelemetryUnavailableError);
  });

  test('caller-owned providers need only the API package', async () => {
    const requested: string[] = [];
    const loader: ModuleLoader = (specifier) => {
      requested.push(specifier);
      return import(specifier);
    };

    await Capture.start({ loadModule: loader });

    expect(requested).toEqual(['@opentelemetry/api']);
  });

  test('without an observer, no telemetry code is loaded', async () => {
    const script = `
      await import(${JSON.stringify(`${REPOSITORY}src/loop/index.ts`)});
      console.log(Object.keys(require.cache).filter((key) => key.includes('@opentelemetry')).length);
    `;

    const process = Bun.spawn([Bun.argv[0] ?? 'bun', '-e', script], { cwd: REPOSITORY, stderr: 'pipe', stdout: 'pipe' });

    expect(await new Response(process.stdout).text()).toBe('0\n');
    expect(await process.exited).toBe(0);
  });

  test('creating an observer loads the telemetry packages', async () => {
    const script = `
      const { otlpObserver } = await import(${JSON.stringify(`${REPOSITORY}src/loop/index.ts`)});
      const observer = await otlpObserver({ endpoint: 'http://127.0.0.1:1' });
      console.log(Object.keys(require.cache).some((key) => key.includes('@opentelemetry')));
      await observer.shutdown();
    `;

    const process = Bun.spawn([Bun.argv[0] ?? 'bun', '-e', script], { cwd: REPOSITORY, stderr: 'pipe', stdout: 'pipe' });

    expect(await new Response(process.stdout).text()).toBe('true\n');
  });
});

interface Received {
  body: unknown;
  path: string;
}

describe('OTLP/HTTP export', () => {
  const servers: ReturnType<typeof Bun.serve>[] = [];

  afterEach(async () => {
    for (const server of servers.splice(0)) {
      await server.stop(true);
    }
  });

  function collector(): { received: Received[]; url: string } {
    const received: Received[] = [];
    const server = Bun.serve({
      fetch: async (request) => {
        received.push({ body: await request.json(), path: new URL(request.url).pathname });
        return Response.json({});
      },
      port: 0,
    });
    servers.push(server);
    return { received, url: `http://localhost:${String(server.port)}/` };
  }

  test('spans and metrics with the documented names and attributes reach the collector', async () => {
    const { received, url } = collector();
    const observer = await otlpObserver({ endpoint: url, resourcePerAgent: true, serviceName: 'dev' });

    observer.worktree(worktree('ready', 0, null));
    observer.agentRun(record({ agent: 'planner' }));
    observer.worktree(worktree('removed', 7, true));
    await observer.shutdown();

    const traces = received.filter((request) => request.path === '/v1/traces');
    const spans = traces.flatMap((request) =>
      (request.body as { resourceSpans: { resource: { attributes: Attr[] }; scopeSpans: { spans: { name: string }[] }[] }[] }).resourceSpans.flatMap(
        (resource) =>
          resource.scopeSpans.flatMap((scope) =>
            scope.spans.map((span) => `${attribute(resource.resource.attributes, 'service.name') ?? ''}: ${span.name}`),
          ),
      ),
    );
    expect(spans.sort()).toEqual(['code: loop.worktree', 'planner: execute_tool bash', 'planner: invoke_agent planner']);
    const metricNames = received
      .filter((request) => request.path === '/v1/metrics')
      .flatMap((request) =>
        (request.body as { resourceMetrics: { scopeMetrics: { metrics: { name: string }[] }[] }[] }).resourceMetrics.flatMap((resource) =>
          resource.scopeMetrics.flatMap((scope) => scope.metrics.map((metric) => metric.name)),
        ),
      );
    expect([...new Set(metricNames)].sort()).toEqual([
      'gen_ai.client.operation.duration',
      'gen_ai.client.token.usage',
      'loop.agent.aiu',
      'loop.agent.premium_requests',
    ]);
  });

  test('an unreachable collector never fails the run and the failure is logged only', async () => {
    setLogSink((entry) => logged.push(entry));
    const observer = await otlpObserver({ endpoint: 'http://127.0.0.1:1', exportTimeoutMs: 500 });

    observer.worktree(worktree('ready', 0, null));
    observer.agentRun(record());
    observer.worktree(worktree('removed', 7, true));

    await observer.forceFlush();
    await observer.shutdown();
    expect(logged.map((entry) => entry.level)).toContain('warning');
    expect(logged.map((entry) => entry.message).join('\n')).toMatch(/telemetry (flush|shutdown) failed/);
  });
});

interface Attr {
  key: string;
  value: { stringValue?: string };
}

function attribute(attributes: Attr[], key: string): string | undefined {
  return attributes.find((entry) => entry.key === key)?.value.stringValue;
}
