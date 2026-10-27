import type * as Api from '@opentelemetry/api';
import type * as MetricExporter from '@opentelemetry/exporter-metrics-otlp-http';
import type * as TraceExporter from '@opentelemetry/exporter-trace-otlp-http';
import type * as Resources from '@opentelemetry/resources';
import type * as SdkMetrics from '@opentelemetry/sdk-metrics';
import type * as SdkTrace from '@opentelemetry/sdk-trace-base';

import { AsyncLocalStorage } from 'node:async_hooks';

import type { PhaseSpan, SpanAttributeValue, SpanObserver } from './spans.ts';
import type { AgentRunRecord, RunObserver, ToolCall, Usage, WorktreeEvent } from './values.ts';

import { getLogger } from '../diagnostics.ts';
import { dumps, type Json, ordered } from '../json.ts';
import { describeError } from '../text.ts';
import { Role } from './roles.ts';

export const SCOPE = 'loop';
export const DEFAULT_SERVICE_NAME = 'loop';

/** The packages the OTLP observer loads, only when it is created; they are optional peers of Loop. */
export const TELEMETRY_PACKAGES: readonly string[] = [
  '@opentelemetry/api',
  '@opentelemetry/exporter-metrics-otlp-http',
  '@opentelemetry/exporter-trace-otlp-http',
  '@opentelemetry/resources',
  '@opentelemetry/sdk-metrics',
  '@opentelemetry/sdk-trace-base',
];

const DEFAULT_EXPORT_TIMEOUT_MS = 10_000;
const PROVIDERS: Readonly<Record<string, string>> = { codex: 'openai', copilot: 'github.copilot' };
const logger = getLogger('loop.observe.otel');

/** Loads an optional module by package name; replaced in tests to simulate packages that are not installed. */
export type ModuleLoader = (specifier: string) => Promise<unknown>;

export interface OtlpObserverOptions {
  /** Export prompts, outputs and tool arguments too. Default false. */
  captureContent?: boolean;
  /** A base URL such as `http://localhost:4318`; unset, the standard `OTEL_EXPORTER_OTLP_*` variables apply. */
  endpoint?: string;
  /** Per-request export timeout; keeps an unreachable collector from stalling the run. Default 10000. */
  exportTimeoutMs?: number;
  /** Replaces the guarded dynamic import of the telemetry packages. */
  loadModule?: ModuleLoader;
  /** A meter provider owned by the caller instead of one exporting over OTLP/HTTP. */
  meterProvider?: Api.MeterProvider;
  /** Export each named agent, each Workflow span role and the Worktree as its own resource. */
  resourcePerAgent?: boolean;
  /** Tracer providers by resource name, owned by the caller; with `resourcePerAgent` they replace the exporting ones. */
  resourceProviders?: (resource: string) => Api.TracerProvider;
  /** The `service.name` resource attribute; defaults to `OTEL_SERVICE_NAME`, then `loop`. */
  serviceName?: string;
  /** A tracer provider owned by the caller instead of one exporting over OTLP/HTTP. */
  tracerProvider?: Api.TracerProvider;
}

interface Instruments {
  readonly aiu: Api.Counter;
  readonly operationDuration: Api.Histogram;
  readonly premiumRequests: Api.Counter;
  readonly tokenUsage: Api.Histogram;
}

interface Lifecycle {
  forceFlush: () => Promise<void>;
  shutdown: () => Promise<void>;
}

interface ObserverParts {
  api: typeof Api;
  captureContent: boolean;
  meterProvider: Api.MeterProvider;
  owned: Set<Lifecycle>;
  resourceProviders: ((resource: string) => Api.TracerProvider) | null;
  tracerProvider: Api.TracerProvider;
}

/** Turns Agent Runs and Worktrees into OpenTelemetry spans and metrics; spans nest under the caller's current span. */
export class OtlpObserver implements RunObserver, SpanObserver {
  readonly #api: typeof Api;
  readonly #captureContent: boolean;
  readonly #instruments: Instruments;
  readonly #owned: Set<Lifecycle>;
  readonly #resourceProviders: ((resource: string) => Api.TracerProvider) | null;
  readonly #scope = new AsyncLocalStorage<Api.Context>();
  readonly #tracer: Api.Tracer;
  readonly #worktrees = new Map<string, Api.Span>();

  /** Use `otlpObserver()`, which loads the telemetry packages and builds the parts. */
  constructor(parts: ObserverParts) {
    this.#api = parts.api;
    this.#tracer = parts.tracerProvider.getTracer(SCOPE);
    this.#resourceProviders = parts.resourceProviders;
    this.#captureContent = parts.captureContent;
    this.#owned = parts.owned;
    this.#instruments = createInstruments(parts.meterProvider.getMeter(SCOPE));
  }

  /** Exports one `invoke_agent` span, its `execute_tool` children and the usage metrics. */
  agentRun(record: AgentRunRecord): void {
    const tracer = this.#resourceTracer(record.agent);
    const span = tracer.startSpan(
      `invoke_agent ${record.agent ?? record.cli}`,
      { attributes: this.#agentAttributes(record), startTime: hrTime(record.startedAt) },
      this.#parentContext(record.cwd),
    );
    for (const tool of record.tools) {
      this.#exportTool(tracer, span, tool);
    }
    const failed = record.error !== null || record.exitCode !== 0;
    if (failed) {
      span.setStatus({
        code: this.#api.SpanStatusCode.ERROR,
        message: record.error === null || record.error === '' ? `exit code ${String(record.exitCode)}` : record.error,
      });
    }
    span.end(hrTime(record.endedAt));
    this.#recordMetrics(record, failed);
  }

  /** Exports what is buffered; a failure is logged, never raised. */
  async forceFlush(): Promise<void> {
    await this.#each('flush', (target) => target.forceFlush());
  }

  /** Flushes and releases the exporters this observer created; a failure is logged, never raised. */
  async shutdown(): Promise<void> {
    await this.#each('shutdown', (target) => target.shutdown());
  }

  /** A Workflow-owned span, exported as resource `role`; Agent Run and Worktree spans started inside it nest under it. */
  span(name: string, attributes: Readonly<Record<string, SpanAttributeValue>>, role: string): PhaseSpan {
    const api = this.#api;
    const parent = this.#current();
    const span = this.#resourceTracer(role).startSpan(name, { attributes: { ...attributes } }, parent);
    let ended = false;
    return {
      end: (final) => {
        if (ended) {
          return;
        }
        ended = true;
        try {
          span.setAttributes({ ...final.attributes });
          for (const gate of final.gates) {
            span.addEvent('loop.gate', {
              'loop.gate.check': gate.check,
              'loop.gate.note': gate.note,
              'loop.gate.ok': gate.ok,
            });
          }
          if (final.error !== null) {
            span.setStatus({ code: api.SpanStatusCode.ERROR, message: final.error });
          }
        } finally {
          span.end();
        }
      },
      run: (body) => {
        const scoped = api.trace.setSpan(parent, span);
        return this.#scope.run(scoped, () => api.context.with(scoped, body));
      },
    };
  }

  /** Holds a worktree span until removal or retention ends the active run. */
  worktree(event: WorktreeEvent): void {
    if (event.point === 'ready') {
      this.#worktrees.set(
        event.path,
        this.#resourceTracer(Role.CODE).startSpan(
          'loop.worktree',
          { attributes: { 'loop.worktree.path': event.path }, startTime: hrTime(event.at) },
          this.#current(),
        ),
      );
      return;
    }
    const span = this.#worktrees.get(event.path);
    if (span === undefined || (event.point !== 'removed' && event.point !== 'retained')) {
      return;
    }
    this.#worktrees.delete(event.path);
    if (event.point === 'retained') {
      span.setAttribute('loop.worktree.retained', true);
    }
    if (event.ok === false) {
      span.setStatus({ code: this.#api.SpanStatusCode.ERROR, message: 'worktree run failed' });
    }
    span.end(hrTime(event.at));
  }

  #agentAttributes(record: AgentRunRecord): Record<string, SpanAttributeValue> {
    const attributes: Record<string, SpanAttributeValue> = {
      'gen_ai.conversation.id': record.session,
      'gen_ai.operation.name': 'invoke_agent',
      'gen_ai.provider.name': providerOf(record.cli),
      'loop.agent.cwd': record.cwd,
    };
    const optional: Record<string, null | SpanAttributeValue | undefined> = {
      'gen_ai.agent.name': record.agent,
      'gen_ai.request.model': record.model,
      'loop.agent.exit_code': record.exitCode,
      'loop.agent.native_session_id': record.nativeId,
      'loop.agent.thinking': record.reasoningEffort,
    };
    for (const [key, value] of Object.entries(optional)) {
      if (value !== null && value !== undefined) {
        attributes[key] = value;
      }
    }
    if (record.usage !== null) {
      Object.assign(attributes, usageAttributes(record.usage));
    }
    if (this.#captureContent) {
      Object.assign(attributes, contentAttributes(record));
    }
    return attributes;
  }

  #current(): Api.Context {
    return this.#scope.getStore() ?? this.#api.context.active();
  }

  async #each(action: string, run: (target: Lifecycle) => Promise<void>): Promise<void> {
    await Promise.all(
      [...this.#owned].map(async (target) => {
        try {
          await run(target);
        } catch (error) {
          logger.warning(`telemetry ${action} failed: ${describeError(error)}`);
        }
      }),
    );
  }

  #exportTool(tracer: Api.Tracer, agent: Api.Span, tool: ToolCall): void {
    const attributes: Record<string, SpanAttributeValue> = {
      'gen_ai.operation.name': 'execute_tool',
      'gen_ai.tool.call.id': tool.callId,
      'gen_ai.tool.name': tool.name,
      'loop.tool.ok': tool.ok,
    };
    if (this.#captureContent) {
      attributes['gen_ai.tool.call.arguments'] = dumps({ ...tool.arguments } as Json);
      attributes['gen_ai.tool.call.result'] = tool.result;
    }
    const span = tracer.startSpan(
      `execute_tool ${tool.name}`,
      { attributes, kind: this.#api.SpanKind.INTERNAL, startTime: hrTime(tool.startedAt) },
      this.#api.trace.setSpan(this.#api.ROOT_CONTEXT, agent),
    );
    span.setStatus({ code: tool.ok ? this.#api.SpanStatusCode.OK : this.#api.SpanStatusCode.ERROR });
    span.end(hrTime(tool.endedAt));
  }

  // Explicit workflow phases take precedence over the worktree fallback.
  #parentContext(cwd: string): Api.Context {
    const current = this.#current();
    const active = this.#api.trace.getSpan(current)?.spanContext();
    if (active !== undefined && this.#api.isSpanContextValid(active)) {
      return current;
    }
    const worktree = this.#worktrees.get(cwd);
    return worktree === undefined ? current : this.#api.trace.setSpan(current, worktree);
  }

  #recordMetrics(record: AgentRunRecord, failed: boolean): void {
    const shared: Record<string, string> = {
      'gen_ai.operation.name': 'invoke_agent',
      'gen_ai.provider.name': providerOf(record.cli),
    };
    if (record.model !== null) {
      shared['gen_ai.request.model'] = record.model;
    }
    this.#instruments.operationDuration.record(
      (record.endedAt.getTime() - record.startedAt.getTime()) / 1000,
      failed ? { ...shared, 'error.type': 'agent_failed' } : shared,
    );
    const usage = record.usage;
    if (usage === null) {
      return;
    }
    this.#instruments.tokenUsage.record(usage.totalInputTokens, { ...shared, 'gen_ai.token.type': 'input' });
    this.#instruments.tokenUsage.record(usage.outputTokens, { ...shared, 'gen_ai.token.type': 'output' });
    this.#instruments.aiu.add(usage.totalAiu, shared);
    this.#instruments.premiumRequests.add(usage.premiumRequests, shared);
  }

  /** The tracer exporting as its own resource when the observer has resource providers, else the shared one. */
  #resourceTracer(resource: null | string): Api.Tracer {
    if (resource === null || this.#resourceProviders === null) {
      return this.#tracer;
    }
    return this.#resourceProviders(resource).getTracer(SCOPE);
  }
}

/** The optional telemetry packages are needed but cannot be loaded. */
export class TelemetryUnavailableError extends Error {
  constructor(options?: ErrorOptions) {
    super(
      'OTLP telemetry export needs the optional OpenTelemetry packages, which are not installed. ' +
        `Install them with: bun add ${TELEMETRY_PACKAGES.join(' ')} ` +
        '(or run without an OTLP endpoint to disable telemetry).',
      options,
    );
    this.name = 'TelemetryUnavailableError';
  }
}

/** Epoch-nanosecond timestamps as the SDK's time type: whole seconds and the nanoseconds within the second. */
export function hrTime(moment: Date): Api.HrTime {
  const nanos = BigInt(moment.getTime()) * 1_000_000n;
  return [Number(nanos / 1_000_000_000n), Number(nanos % 1_000_000_000n)];
}

/**
 * An observer exporting over OTLP/HTTP. The telemetry packages load here, behind a guard: when they are not
 * installed this rejects with `TelemetryUnavailableError`, which names the packages to install.
 */
export async function otlpObserver(options: OtlpObserverOptions = {}): Promise<OtlpObserver> {
  const load = options.loadModule ?? importModule;
  const api = await loadPackage<typeof Api>(load, '@opentelemetry/api');
  const owned = new Set<Lifecycle>();
  const own = <T extends Lifecycle>(provider: T): T => {
    owned.add(provider);
    return provider;
  };
  let tracerProvider = options.tracerProvider;
  let meterProvider = options.meterProvider;
  let resourceProviders = options.resourceProviders ?? null;
  const perAgent = options.resourcePerAgent === true && resourceProviders === null;
  if (tracerProvider === undefined || meterProvider === undefined || perAgent) {
    const exporting = await loadExporting(load, options);
    tracerProvider ??= own(exporting.tracerProvider(null));
    meterProvider ??= own(exporting.meterProvider());
    if (perAgent) {
      const providers = new Map<string, Api.TracerProvider>();
      resourceProviders = (resource) => {
        const existing = providers.get(resource) ?? own(exporting.tracerProvider(resource));
        providers.set(resource, existing);
        return existing;
      };
    }
  }
  return new OtlpObserver({
    api,
    captureContent: options.captureContent ?? false,
    meterProvider,
    owned,
    resourceProviders,
    tracerProvider,
  });
}

function contentAttributes(record: AgentRunRecord): Record<string, string> {
  const message = (role: string, content: string): Json =>
    ordered([
      ['role', role],
      ['parts', [ordered([['type', 'text'], ['content', content]])]],
    ]);
  const attributes: Record<string, string> = { 'gen_ai.input.messages': dumps([message('user', record.prompt)]) };
  if (record.output !== null) {
    attributes['gen_ai.output.messages'] = dumps([message('assistant', record.output)]);
  }
  return attributes;
}

function createInstruments(meter: Api.Meter): Instruments {
  return {
    aiu: meter.createCounter('loop.agent.aiu', { description: 'GitHub AI units the agent consumed.', unit: '{AIU}' }),
    operationDuration: meter.createHistogram('gen_ai.client.operation.duration', {
      description: 'Duration of an agent invocation.',
      unit: 's',
    }),
    premiumRequests: meter.createCounter('loop.agent.premium_requests', {
      description: "Premium requests the user's prompts billed.",
      unit: '{request}',
    }),
    tokenUsage: meter.createHistogram('gen_ai.client.token.usage', {
      description: 'Tokens used per agent invocation, by token type.',
      unit: '{token}',
    }),
  };
}

// `import()` of a package name that is only known at run time stays out of the static import graph.
async function importModule(specifier: string): Promise<unknown> {
  return import(specifier);
}

async function loadExporting(load: ModuleLoader, options: OtlpObserverOptions) {
  const resources = await loadPackage<typeof Resources>(load, '@opentelemetry/resources');
  const sdkTrace = await loadPackage<typeof SdkTrace>(load, '@opentelemetry/sdk-trace-base');
  const sdkMetrics = await loadPackage<typeof SdkMetrics>(load, '@opentelemetry/sdk-metrics');
  const traceExporter = await loadPackage<typeof TraceExporter>(load, '@opentelemetry/exporter-trace-otlp-http');
  const metricExporter = await loadPackage<typeof MetricExporter>(load, '@opentelemetry/exporter-metrics-otlp-http');
  const timeoutMillis = options.exportTimeoutMs ?? DEFAULT_EXPORT_TIMEOUT_MS;
  const serviceName = serviceNameOf(options.serviceName);
  const perAgent = options.resourcePerAgent === true;
  // Resource-per-agent backends list each resource by name, so a fixed instance id drops the per-process suffix.
  const resourceOf = (name: null | string): Resources.Resource =>
    resources.resourceFromAttributes({
      'service.name': name ?? serviceName,
      ...(name !== null || perAgent ? { 'service.instance.id': name ?? serviceName } : {}),
    });
  return {
    meterProvider: () =>
      new sdkMetrics.MeterProvider({
        readers: [
          new sdkMetrics.PeriodicExportingMetricReader({
            exporter: new metricExporter.OTLPMetricExporter({
              timeoutMillis,
              url: signalEndpoint(options.endpoint, 'metrics'),
            }),
            exportTimeoutMillis: timeoutMillis,
          }),
        ],
        resource: resourceOf(null),
      }),
    tracerProvider: (name: null | string) =>
      new sdkTrace.BasicTracerProvider({
        resource: resourceOf(name),
        spanProcessors: [
          new sdkTrace.BatchSpanProcessor(
            new traceExporter.OTLPTraceExporter({ timeoutMillis, url: signalEndpoint(options.endpoint, 'traces') }),
          ),
        ],
      }),
  };
}

async function loadPackage<T>(load: ModuleLoader, name: string): Promise<T> {
  try {
    return (await load(name)) as T;
  } catch (error) {
    throw new TelemetryUnavailableError({ cause: error });
  }
}

function providerOf(cli: string): string {
  return PROVIDERS[cli] ?? cli;
}

function serviceNameOf(name: string | undefined): string {
  const fromEnvironment = process.env.OTEL_SERVICE_NAME;
  return [name, fromEnvironment].find((value) => value !== undefined && value !== '') ?? DEFAULT_SERVICE_NAME;
}

function signalEndpoint(endpoint: string | undefined, signal: string): string | undefined {
  return endpoint === undefined ? undefined : `${endpoint.replace(/\/+$/, '')}/v1/${signal}`;
}

function usageAttributes(usage: Usage): Record<string, number> {
  const attributes: Record<string, number> = {
    'gen_ai.usage.input_tokens': usage.totalInputTokens,
    'gen_ai.usage.output_tokens': usage.outputTokens,
    'loop.agent.api_calls': usage.apiCalls,
    'loop.usage.cache_read_tokens': usage.cacheReadTokens,
    'loop.usage.cache_write_tokens': usage.cacheWriteTokens,
    'loop.usage.input_tokens': usage.inputTokens,
    'loop.usage.output_tokens': usage.outputTokens,
    'loop.usage.premium_requests': usage.premiumRequests,
    'loop.usage.reasoning_tokens': usage.reasoningTokens,
    'loop.usage.total_aiu': usage.totalAiu,
    'loop.usage.total_tokens': usage.totalInputTokens + usage.outputTokens,
  };
  for (const [kind, value] of Object.entries(usage.aiu)) {
    attributes[`loop.usage.${kind}_aiu`] = value;
  }
  return attributes;
}

