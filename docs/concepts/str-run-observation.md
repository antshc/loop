# Run Observation
**Type:** Architecture Pattern

## Purpose

Let an operator see what Agent Runs and Worktrees did — tool calls with real timings, token, AIU, and premium-request usage — in an OpenTelemetry dashboard such as Aspire, without every Workflow parsing agent CLI logs and without making telemetry a hard dependency.

## Concept

A Workflow opts in with `Agent().with_observer(observer)`. Loop then decorates its CLI runner and Git service: after each Agent Run turn the observer receives an `AgentRunRecord` (CLI, session, times, exit code, model, `Usage`, `ToolCall`s), and around each Worktree it receives `WorktreeEvent`s (`ready`, `removing`, `removed`). Usage and tool calls come from a per-CLI `SessionLogReader` that reads the CLI's own session log after the process exits; for Copilot a Start turn is pinned to a session id derived from the session name so the log path is known.

`otlp_observer(endpoint=..., service_name=...)` (extra `loop[otel]`) is the OpenTelemetry observer. It exports an `invoke_agent` span per turn with `execute_tool` children, a `loop.worktree` span, and the `gen_ai.client.*`, `loop.agent.aiu`, and `loop.agent.premium_requests` metrics over OTLP/HTTP. Agent and Worktree spans nest under the current OpenTelemetry span, so a Workflow adds Spec, Ticket, phase, or gate spans around its runs with `observer.span(name, attributes)`; Loop never learns those concepts. An `AgentProfile` may carry a `name`; with `otlp_observer(resource_per_agent=True)` each named agent exports as its own resource, so a backend such as Aspire colors the agents apart. The reusable names are `Role`: `engineer` (the request), `code` (the Worktree and deterministic Workflow steps, via `observer.span(name, role=Role.CODE)`), `planner`, `developer` and `reviewer`. Decision: [Observe Agent Runs through an optional observer seam](../adr/observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md).

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

## Factory spans

The factory runtime owns its spans and does not use Loop's Phase reporting ([decision](../adr/report-factory-runs-as-opentelemetry-spans-bypassing-loops-phase-reporting.md)). It opens them through a `Spans` seam, which `LogSpans` (console), `RecordingSpans` (tests), `NullSpans` and `OtelSpans` (over the Workflow's `observer.span(...)`) implement, so Loop's `invoke_agent`, `execute_tool` and `loop.worktree` spans nest under the current attempt automatically.

| Span | Name | Parent | Role | Key attributes and events |
|---|---|---|---|---|
| Run | the Workflow's name | none | `engineer` | `factory.workflow`, `factory.work_item`, `factory.epoch`, `factory.run.status` and `factory.run.reason`, plus attributes a hand-off records; event `factory.resume` |
| Scope | the flow and item, such as `ticket #12` | run or outer scope | `code` | `factory.scope`, `factory.item`, `factory.revision`, `factory.scope.status`; events for skipped or replayed Stages, dispatch, reroute and pure gates |
| Stage attempt | the Stage id, such as `implement-ticket` | scope | the agent's role, or `code` | `factory.stage.id`, `factory.stage.attempt`, `factory.stage.executor`, `factory.stage.contract`, `factory.stage.outcome` (pass, fail, hold), `factory.stage.reason`; events for gate, contract, verify, commit, restore, reconcile, rework and hand-off |

- Pure Stages and Selectors open no span; their verdicts are events on the scope span.
- A scope span opens lazily, on its first child span or event that is not a quiet pass, so a quiet poll exports nothing.
- The status is error on a failed or handed-off run or scope and on a failed attempt; a hold is not an error.
- `factory.*` is the attribute namespace; `loop.gate` events are not emitted.
- Observation never changes a run's result: a failing sink is logged and the run continues.
- The console is one line per finished span; the Rich terminal table (`--visualize`) is dropped for the factory.

## Optional Workflow phase reporting

`WorkflowBuilder` on the public `loop` package declares phases while ordinary Python
owns execution. It serves user-written scripts; factory Workflows report Stage attempt spans instead. `.console()` emits high-level transitions, `.visualize()` adds an
optional Rich terminal table, and `.otel(observer)` exports workflow-owned spans.
All three options are independent. Direct `Phases()` and existing workflow spans
remain supported without the builder.

Use the same observer with `Agent().with_observer(observer)` to connect phase and
Agent Run traces. An active span takes precedence over the worktree fallback when
parenting an Agent Run. Gate failures and explicit business outcomes are exported;
the caller decides whether a failed attempt can recover or fails the whole run.

See [Optional Workflow Builder](../../USAGE.md#optional-workflow-builder) for the
runnable fake e-commerce example, lifecycle rules, and independent reporting options.
