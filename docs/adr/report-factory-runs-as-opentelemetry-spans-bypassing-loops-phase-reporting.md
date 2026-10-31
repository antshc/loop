# Report Workflow runs as runtime OpenTelemetry spans and retire Loop's Phase reporting

The Workflow runtime executes Stages and must show an operator what happened: which Stage ran, how many attempts it took, why it failed or held, and what the agents did inside it. Loop once offered optional Phase reporting (`WorkflowBuilder`, `Workflow`, `WorkflowRun`, `Phases`, `Phase`, `Kind`) for plain-script Workflows and, through the observer seam, `invoke_agent`, `execute_tool` and `loop.worktree` spans that nest under the current span ([Observe Agent Runs through an optional observer seam](observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md)). The runtime derives its spans itself, and since the runtime is Loop's one Workflow architecture, Phase reporting and its demo are retired. Applies to every Workflow run by Loop's runtime. The reporting model is in [Run Observation](../concepts/str-run-observation.md#runtime-spans).

## Considered Options

- **Wrap Phase reporting** (open a Phase per Stage) — rejected:
  - `Phase.gate` is boolean, so it cannot express *hold*;
  - a phase failure is sticky, so it cannot show "attempt 1 failed, attempt 2 passed" as one Stage;
  - a `WorkflowRun` needs a parallel declaration list of phases, which is the hand-synced list the definition removes;
  - wrapping would keep two reporting vocabularies side by side.
- **Extend Phase reporting in the library** (hold outcomes, non-sticky failure, derived declarations) — rejected: it duplicates what the runtime's spans already derive from the definition.
- **Keep Phase reporting in the library for plain-script Workflows beside the runtime's spans** (the previous decision) — rejected: two Workflow architectures and two reporting vocabularies; the runtime's `Workflow` also took the public name.
- **The runtime opens its own spans through a small `Spans` seam over the existing observer; Phase reporting is removed (chosen).**

## Decision

- The runtime reports three kinds of span through `Spans`: the **run** (named after the Workflow, role `engineer`), a **Scope** span for each flow scope (role `code`) and a **Stage attempt** span per attempt (the agent's role, or `code`). Loop's `invoke_agent`, `execute_tool` and `loop.worktree` spans nest under the current attempt automatically. Span names and attributes are derived from the definition and use the `factory.*` namespace.
- Pure Stages and Selectors open no span; their verdicts are events on the scope span. Scope spans open lazily on the first child span or event that is not a quiet pass, so a quiet poll exports nothing.
- A span is an error on a failed or handed-off run or scope and on a failed attempt; hold is not an error.
- `WorkflowBuilder`, `WorkflowRun`, `Phases`, `Phase`, `Kind`, their adapters and the `workflow_reporting.py` demo are removed; an architecture test scans the factory for those names, because import-linter cannot forbid names.
- Implementations: `LogSpans` (one console line per finished span), `RecordingSpans` (tests) and `NullSpans` in the runtime, and `OtelSpans` in the factory's telemetry module, which exports through the Workflow's observer.

## Consequences

- The Rich terminal table (`--visualize`) is gone; the span log and any OTLP backend replace it.
- Console lines and telemetry names are Stage ids, and `loop.gate` events are not emitted.
- Observation never changes a run's result: opening, writing to and ending a span is isolated and logged, and the runtime does not depend on an exporter.
