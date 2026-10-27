# Observe Agent Runs through an optional observer seam that exports to OpenTelemetry

Operators want to see what an Agent Run did — its tool calls, token and AIU usage, and its Worktree — in a trace dashboard such as Aspire. Loop's `CliOutcome` carries none of that: usage and tool timings exist only in the agent CLI's own session log, and Tickets, phases, and gates are Workflow concepts, not library ones. The [Aspire prototype](../prototypes/dashboard-aspire/README.md) mirrored the dev Workflow to get it; this decision moves the reusable part into the library without making telemetry a dependency of every Workflow.

## Considered Options

- **Keep telemetry in each Workflow, as the prototype does** — rejected: every Workflow re-implements session-log parsing and the span vocabulary, and the log format leaks into Workflow code.
- **Auto-enable from `OTEL_EXPORTER_OTLP_ENDPOINT`** — rejected: a library that starts exporting because an environment variable exists is surprising and breaks "optional".
- **A new `AgentCli` member returning usage, or extra fields on `CliOutcome`** — rejected: it changes an interface every adapter implements and every test fake builds, for data only some CLIs can supply.
- **Library-owned session, Ticket, phase, and gate spans** — rejected: those are Workflow concepts ([Ship Loop as a workflow library](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- **An observer seam with an OpenTelemetry adapter, opted into by the Workflow (chosen).**

## Decision

`AgentBuilder.withObserver(observer)` reports every Agent Run turn and Worktree to a `RunObserver` (`agentRun(record)`, `worktree(event)`). Without it the library behaves exactly as before: same CLI arguments, no OpenTelemetry import, no new hard dependency.

- **Seam.** `ObservingCliRunner` decorates the existing `CliRunner`; `ObservingGitService` decorates `GitService`. The values (`AgentRunRecord`, `Usage`, `ToolCall`, `WorktreeEvent`) live in `src/loop/observe/`, below `sessions`, and import no third-party package.
- **Session logs.** A `SessionLogReader` per CLI, keyed by `AgentCli.name`, turns the CLI's own log into usage and tool calls. Only the Copilot reader exists; a Codex Agent Run is reported with `usage: null`.
- **Finding the log.** On a Start turn the Copilot reader adds `--session-id <uuid5 of the session name>` beside `--name`; Resume stays `--resume=<name>`. The two flags combine and resume reaches the pinned session ([experiment](../experiments/copilot-cli-session-id-with-name-and-resume.md)). A turn is the log's events from its last `session.start` or `session.resume`; `session.shutdown` totals are cumulative and never summed.
- **OpenTelemetry.** `otlpObserver({ endpoint, serviceName, captureContent })` (async, `captureContent` defaults to `false`) lives in `src/loop/observe/otel.ts`. The `@opentelemetry/*` packages are optional peer dependencies, loaded by a guarded dynamic import inside that module only, which is the only library module that imports them (checked by the architecture contracts). It exports `invoke_agent`, `execute_tool`, and `loop.worktree` spans with the OpenTelemetry GenAI attribute names plus `loop.*` ones, and token, duration, AIU, and premium-request metrics, over OTLP/HTTP, so Aspire and any other OTLP backend work. Prompts, outputs, and tool arguments are exported only with `captureContent: true`.
- **Workflow spans.** A Workflow opens its own spans (Spec, Ticket) with `observer.span(...)`; the library's spans nest under the current span.
- **Failure policy.** An observer or reader failure, and a telemetry flush or shutdown failure, is logged and never changes a run's result. Missing or unreadable logs give `usage: null`, never zero.

## Consequences

- Telemetry is opt-in per builder and needs the optional OpenTelemetry packages; importing `loop` works without them, and `otlpObserver()` rejects with a `TelemetryUnavailableError` naming the packages to install.
- Starting a session with an observer changes the CLI arguments (`--session-id`). A session started without an observer has no pinned id, so a later observed Resume reports `usage: null`.
- The Copilot log format is undocumented; golden-log tests and the `usage: null` fallback bound the damage when it changes.
- Spans are exported when they end and tool and usage data arrive after the CLI exits, so a dashboard shows an Agent Run only once it finishes.
- One writer per session is assumed; concurrent resumes of one session are unsupported.
- `workflows/dev` opts in with `--otlp-endpoint` and adds its own Spec and Ticket spans; Aspire-specific per-role colouring stays a Workflow concern.
