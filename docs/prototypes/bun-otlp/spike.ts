import { context, trace, SpanStatusCode, type Span } from "@opentelemetry/api";
import { AsyncLocalStorageContextManager } from "@opentelemetry/context-async-hooks";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BasicTracerProvider, BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { MeterProvider, PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";

const endpoint = process.env.OTLP_ENDPOINT ?? "http://127.0.0.1:4318";
const timeoutMs = Number(process.env.EXPORT_TIMEOUT_MS ?? 10000);
const t0 = performance.now();
const log = (msg: string) => console.log(`[${(performance.now() - t0).toFixed(0)}ms] ${msg}`);

context.setGlobalContextManager(new AsyncLocalStorageContextManager().enable());

const resource = resourceFromAttributes({ "service.name": "loop-bun-spike" });
const traceExporter = new OTLPTraceExporter({ url: `${endpoint}/v1/traces`, timeoutMillis: timeoutMs });
const provider = new BasicTracerProvider({ resource, spanProcessors: [new BatchSpanProcessor(traceExporter)] });
trace.setGlobalTracerProvider(provider);

const meterProvider = new MeterProvider({
  resource,
  readers: [
    new PeriodicExportingMetricReader({
      exporter: new OTLPMetricExporter({ url: `${endpoint}/v1/metrics`, timeoutMillis: timeoutMs }),
      exportIntervalMillis: 60000,
    }),
  ],
});

const tracer = trace.getTracer("loop");
const meter = meterProvider.getMeter("loop");
const tokens = meter.createHistogram("gen_ai.client.token.usage", { unit: "{token}" });

async function inSpan<T>(name: string, attributes: Record<string, string | number | boolean>, fn: (s: Span) => Promise<T>) {
  return tracer.startActiveSpan(name, { attributes }, async (span) => {
    try {
      return await fn(span);
    } finally {
      span.end();
    }
  });
}

await inSpan(
  "invoke_agent dev",
  {
    "gen_ai.operation.name": "invoke_agent",
    "gen_ai.provider.name": "github.copilot",
    "gen_ai.conversation.id": "spike-session",
    "gen_ai.agent.name": "dev",
    "gen_ai.request.model": "gpt-5",
    "loop.agent.cwd": process.cwd(),
    "loop.agent.exit_code": 0,
    "loop.usage.input_tokens": 120,
    "loop.usage.output_tokens": 45,
  },
  async (agent) => {
    // Nested spans across awaits/timers/promises exercise AsyncLocalStorage propagation.
    await new Promise((r) => setTimeout(r, 20));
    await inSpan("execute_tool bash", { "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "bash", "loop.tool.ok": true }, async (tool) => {
      await new Promise((r) => setTimeout(r, 10));
      await inSpan("execute_tool nested", { "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "inner" }, async () => {
        await Promise.all([1, 2].map((i) => new Promise((r) => setTimeout(r, i))));
      });
      tool.setStatus({ code: SpanStatusCode.OK });
    });
    const active = trace.getActiveSpan()?.spanContext().spanId;
    log(`active span after nested awaits is agent span: ${active === agent.spanContext().spanId}`);
    tokens.record(120, { "gen_ai.operation.name": "invoke_agent", "gen_ai.token.type": "input" });
    tokens.record(45, { "gen_ai.operation.name": "invoke_agent", "gen_ai.token.type": "output" });
    agent.setStatus({ code: SpanStatusCode.OK });
  },
);

log("flushing");
const f0 = performance.now();
// forceFlush/shutdown reject when the export fails; telemetry must never crash the host process.
const safe = async (name: string, p: Promise<unknown>) => {
  try {
    await p;
    log(`${name} ok in ${(performance.now() - f0).toFixed(0)}ms`);
  } catch (e) {
    log(`${name} FAILED in ${(performance.now() - f0).toFixed(0)}ms: ${(e as Error).message}`);
  }
};
await safe("trace forceFlush", provider.forceFlush());
await safe("metrics forceFlush", meterProvider.forceFlush());
await safe("trace shutdown", provider.shutdown());
await safe("metrics shutdown", meterProvider.shutdown());
log("shutdown complete");
