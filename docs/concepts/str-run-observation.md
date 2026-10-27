# Run Observation
**Type:** Architecture Pattern

## Purpose

Let an operator see what Agent Runs and Worktrees did — tool calls with real timings, token, AIU, and premium-request usage — in an OpenTelemetry dashboard such as Aspire, without every Workflow parsing agent CLI logs and without making telemetry a hard dependency.

## Concept

A Workflow opts in with `Agent().withObserver(observer)`. Loop then decorates its CLI runner and Git service: after each Agent Run turn the observer receives an `AgentRunRecord` (CLI, session, times, exit code, model, `Usage`, `ToolCall`s), and around each Worktree it receives `WorktreeEvent`s (`ready`, `removing`, `removed`, `retained`). Usage and tool calls come from a per-CLI `SessionLogReader` that reads the CLI's own session log after the process exits; for Copilot a Start turn is pinned to a session id derived from the session name so the log path is known.

`await otlpObserver({ endpoint, serviceName })` is the OpenTelemetry observer. It exports an `invoke_agent` span per turn with `execute_tool` children, a `loop.worktree` span, and the `gen_ai.client.*`, `loop.agent.aiu`, and `loop.agent.premium_requests` metrics over OTLP/HTTP. The `@opentelemetry/*` packages are optional peer dependencies, loaded by a guarded dynamic import inside that observer only; when they are missing it rejects with `TelemetryUnavailableError` naming the packages to install. Agent and Worktree spans nest under the current OpenTelemetry span, so a Workflow adds Spec, Ticket, phase, or gate spans around its runs with `observer.span(name, attributes, role)`; Loop never learns those concepts. An `AgentProfile` may carry a `name`; with `otlpObserver({ resourcePerAgent: true })` each named agent exports as its own resource, so a backend such as Aspire colors the agents apart. The reusable names are `Role`: `engineer` (the request), `code` (the Worktree and deterministic Workflow steps, via `observer.span(name, attributes, Role.CODE)`), `planner`, `developer` and `reviewer`. Decision: [Observe Agent Runs through an optional observer seam](../adr/observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md).

## Rules

- MUST keep observation opt-in: without `withObserver`, the CLI arguments, behaviour, and imports are unchanged, and no telemetry code is loaded.
- MUST import the OpenTelemetry packages only in `src/loop/observe/otel.ts`, behind a guarded dynamic import; the architecture contracts fail any other importer.
- MUST NOT let an observer, session-log, flush, or shutdown failure change an Agent Run's result; log it and report `usage: null` where usage is unobservable.
- MUST report unobservable usage as `null`, never zero.
- MUST sum only the events of the turn being reported; session shutdown totals are cumulative.
- MUST export prompts, outputs, and tool arguments only when content capture is requested.
- MUST NOT add Ticket, phase, or gate concepts to the library; Workflows own those spans.

## Benefits and Trade-offs

**Benefits**

- One decorator observes every CLI and agent wrapper; a new sink or CLI is one adapter.
- Any OTLP backend works; Aspire is only an endpoint.

**Trade-offs**

- The Copilot log format is undocumented and may change between CLI versions.
- Spans appear when they end, and tool and usage data arrive after the CLI exits, so there is no live view of a running agent.
- Only Copilot has a session-log reader; Codex Agent Runs carry no usage.
- The OpenTelemetry packages are not officially supported on Bun, and export is verified against in-memory exporters, not a real collector.

## Validation

With a fake `CliRunner`, a golden Copilot session-log fixture, and OpenTelemetry's in-memory exporters, a run yields one `invoke_agent` span with its `execute_tool` children, usage attributes, and metrics, nested under the caller's span; a throwing observer or garbled log leaves the run's result unchanged; with an injected module loader that cannot find the packages, `loop` imports and `otlpObserver()` reports the missing packages only when called.

## Optional Workflow phase reporting

`WorkflowBuilder` on the public `loop` entry point declares phases while ordinary TypeScript
owns execution. `.console()` emits high-level transitions, `.visualize()` adds a built-in
terminal table with no extra dependency, and `.otel(observer)` exports workflow-owned spans.
All three options are independent. Direct `Phases()` and existing workflow spans
remain supported without the builder.

Use the same observer with `Agent().withObserver(observer)` to connect phase and
Agent Run traces. An active span takes precedence over the worktree fallback when
parenting an Agent Run; phase nesting follows the async call chain. Gate failures and explicit business outcomes are exported;
the caller decides whether a failed attempt can recover or fails the whole run.

See [Optional Workflow Builder](../../USAGE.md#optional-workflow-builder) for the
runnable fake e-commerce example, lifecycle rules, and independent reporting options.
