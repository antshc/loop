# Report factory runs as OpenTelemetry spans, bypassing Loop's Phase reporting

The factory runtime executes Stages and must show an operator what happened: which Stage ran, how many attempts it took, why it failed or held, and what the agents did inside it. Loop already offers optional Workflow reporting (`WorkflowBuilder`, `Workflow`, `WorkflowRun`, `Phases`, `Phase`, `Kind`) and, through the observer seam, `invoke_agent`, `execute_tool` and `loop.worktree` spans that nest under the current span ([Observe Agent Runs through an optional observer seam](observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md)). That decision made Workflows own their spans; this one records that the factory runtime derives its spans itself and does not use Phase reporting. The reporting model is in [Run Observation](../concepts/str-run-observation.md#factory-spans).

## Considered Options

- **Wrap Phase reporting** (open a Phase per Stage) — rejected:
  - `Phase.gate` is boolean, so it cannot express *hold*;
  - a phase failure is sticky, so it cannot show "attempt 1 failed, attempt 2 passed" as one Stage;
  - a `WorkflowRun` needs a parallel declaration list of phases, which is the hand-synced list the definition removes;
  - wrapping would keep two reporting vocabularies side by side.
- **Extend Phase reporting in the library** (hold outcomes, non-sticky failure, derived declarations) — rejected: it would teach Loop Stage, hold and Route concepts, which the library does not own.
- **The runtime opens its own spans through a small `Spans` seam over the existing observer (chosen).**

## Decision

- The runtime reports three kinds of span through `Spans`: the **run** (named after the Workflow, role `engineer`), a **Scope** span for each flow scope (role `code`) and a **Stage attempt** span per attempt (the agent's role, or `code`). Loop's `invoke_agent`, `execute_tool` and `loop.worktree` spans nest under the current attempt automatically. Span names and attributes are derived from the definition and use the `factory.*` namespace.
- Pure Stages and Selectors open no span; their verdicts are events on the scope span. Scope spans open lazily on the first child span or event that is not a quiet pass, so a quiet poll exports nothing.
- A span is an error on a failed or handed-off run or scope and on a failed attempt; hold is not an error.
- The factory never imports Phase reporting. An architecture test scans the factory's source for those names from `loop`, because import-linter cannot forbid names.
- Implementations: `LogSpans` (one console line per finished span), `RecordingSpans` (tests), `NullSpans`, and `OtelSpans` in `software_factory/shared/telemetry.py`, which exports through the Workflow's observer.
- `loop.workflow` stays in the library unchanged for user scripts such as `workflows/workflow_reporting.py`.

## Consequences

- The Phase vocabulary stays a Loop-library term; the glossary says to avoid it in the factory and use a Stage attempt span.
- The Rich terminal table (`--visualize`) is dropped for the factory; the span log and any OTLP backend replace it.
- Console lines and telemetry names change from phase names to Stage ids, and `loop.gate` events are not emitted.
- Observation never changes a run's result: opening, writing to and ending a span is isolated and logged, and the runtime does not depend on an exporter.
- Two reporting vocabularies exist in the repository: Phases for user scripts, spans for the factory.
