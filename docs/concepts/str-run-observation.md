# Run Observation
**Type:** Architecture Pattern

## Purpose

Let an operator see what Agent Runs and Worktrees did — tool calls with real timings, token, AIU, and premium-request usage — in an OpenTelemetry dashboard such as Aspire, without every Workflow parsing agent CLI logs and without making telemetry a hard dependency.

## Concept

A Workflow opts in with `Agent().with_observer(observer)`. Loop then decorates its CLI runner and Git service: after each Agent Run turn the observer receives an `AgentRunRecord` (CLI, session, times, exit code, model, `Usage`, `ToolCall`s), and around each Worktree it receives `WorktreeEvent`s (`ready`, `removing`, `removed`). Usage and tool calls come from a per-CLI `SessionLogReader` that reads the CLI's own session log after the process exits; for Copilot a Start turn is pinned to a session id derived from the session name so the log path is known.

`otlp_observer(endpoint=..., service_name=...)` (extra `loop[otel]`) is the OpenTelemetry observer. It exports an `invoke_agent` span per turn with `execute_tool` children, a `loop.worktree` span, and the `gen_ai.client.*`, `loop.agent.aiu`, and `loop.agent.premium_requests` metrics over OTLP/HTTP. Agent and Worktree spans nest under the current OpenTelemetry span, so a Workflow adds Spec, Ticket, phase, or gate spans around its runs with `observer.span(name, attributes)`; Loop never learns those concepts. Decision: [Observe Agent Runs through an optional observer seam](../adr/observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md).

## Rules

- MUST keep observation opt-in: without `with_observer`, the CLI arguments, behaviour, and imports are unchanged.
- MUST import `opentelemetry` only in `loop.observe.otel`, lazily, behind the `loop[otel]` extra.
- MUST NOT let an observer or session-log failure change an Agent Run's result; log it and report `usage=None`.
- MUST report unobservable usage as `None`, never zero.
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

## Validation

With a fake `CliRunner`, a golden `events.jsonl`, and OpenTelemetry's in-memory exporters, a run yields one `invoke_agent` span with its `execute_tool` children, usage attributes, and metrics, nested under the caller's span; a throwing observer or garbled log leaves the run's result unchanged; a build without the extra imports `loop` and reports the missing extra only when `otlp_observer()` is called.
