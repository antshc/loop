# Bun OTLP/HTTP export spike (issue #122)

## Question

Does manual-span OTLP/HTTP export work end-to-end under Bun (1.4.4), including `bun build --compile`, without export hangs (cf. open-telemetry/opentelemetry-js#5260) and with AsyncLocalStorage parent/child propagation?

## Method

`./run.sh` (one command; needs only Bun) starts a tiny Bun OTLP/HTTP receiver (`receiver.ts`, records requests to `out/received.jsonl`), then runs `spike.ts` as `bun run` and as a `bun build --compile` binary. `verify.ts` asserts on what the receiver saw.

- Packages: `@opentelemetry/api` 1.10, `sdk-trace-base` 2.12, `exporter-trace-otlp-http` 0.300, `resources` 3.0, `context-async-hooks` 3.0, `sdk-metrics` 3.0, `exporter-metrics-otlp-http` 0.300.
- Spike: `AsyncLocalStorageContextManager` registered globally; `BatchSpanProcessor` + OTLP trace exporter; one `invoke_agent dev` span with Loop's attributes (`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.conversation.id`, `gen_ai.agent.name`, `gen_ai.request.model`, `loop.agent.cwd`, `loop.agent.exit_code`, usage), via `startActiveSpan`, with nested `execute_tool bash` and `execute_tool nested` spans created after `await`/`setTimeout`/`Promise.all`; one `gen_ai.client.token.usage` histogram; then `forceFlush` + `shutdown` for both providers.
- Failure probes (export timeout 2 s): connection refused, and a receiver that accepts but never answers.

## Observation

- `bun run` and the compiled binary (105 MB, not committed) both delivered 1 trace request (JSON) and metrics requests (forceFlush + shutdown); 3 spans, one traceId. 16 assertions PASS.
- Parent/child via AsyncLocalStorage correct: agent is root, tool's parent = agent, nested's parent = tool; `getActiveSpan()` after nested awaits is the agent span. Works in both modes.
- Flush latency ~20–40 ms locally.
- No hang: refused endpoint and never-responding endpoint both honoured the 2 s `timeoutMillis` (flush failed after ~2.0 s, process exit 0 in 3.5 s / 6.2 s total; metrics+shutdown each re-attempt, so worst case ≈ 3× timeout).
- Gotcha: `forceFlush()` rejects when the export fails (the first run crashed with an unhandled `ECONNREFUSED` until wrapped in try/catch). The error `message` was `undefined` (aggregate error); log the error object instead.
- Gotcha: `sdk-trace-base` is flagged deprecated on npm in favour of `@opentelemetry/sdk-trace`; the 0.300/2.x/3.x package versions are mutually compatible as installed.

## Conclusion

Manual-span OTLP/HTTP export works under Bun, both interpreted and compiled, and AsyncLocalStorage context propagation works, so the TS port can mirror the Python observer (`invoke_agent` + `execute_tool` children, `startActiveSpan` for Workflow spans) on the standard JS OTel SDK without a Bun-specific shim. The #5260 hang was not reproduced.

Implications for the port / optional OTel packaging:
- OTel can be an optional dependency; compiled binaries bundle it fully (size 105 MB is mostly Bun runtime), so dynamic import gating is a size-neutral choice for `--compile`, not a requirement.
- Always set an explicit export `timeoutMillis`, and wrap `forceFlush`/`shutdown` in try/catch so telemetry failures never fail a Workflow run; budget a shutdown deadline of a few multiples of the timeout.
- Register `AsyncLocalStorageContextManager` explicitly (no auto-registration with `BasicTracerProvider`).
- Not tested: protobuf encoding (exporter used JSON), gRPC, real collector/Aspire dashboard, Node runtime comparison.

## Evidence

`evidence.txt` (full `run.sh` output), `run.sh`, `spike.ts`, `receiver.ts`, `verify.ts`, `bun.lock`.

## Cleanup

`run.sh` kills its receivers and deletes the binary; `out/` and `node_modules/` are gitignored. No containers were used.
