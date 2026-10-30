# Compose factory Workflows as declarative definitions over a Stages library, run by a factory runtime

`implement_spec` and `pr_babysit` were vertical slices: feature packages of classes wired by a Dishka composition root, each with its own control flow, its own hand-kept list of phases, and its own copy of the same rework, commit-validation, hand-off and recovery logic. A third Workflow (`address_once`) would have copied the babysit address path again, and no slice could say what an interrupted run had already done. The factory now keeps each Workflow as one **declarative definition** over a shared library of **Stages**, executed by a **factory runtime** in `software_factory/`. This supersedes [Structure workflows as vertical slices over a shared package, wired by a Dishka composition root](structure-workflows-as-vertical-slices-over-a-shared-package-wired-by-dishka.md) for the factory: vertical slices, per-slice composition roots, Dishka and override providers are retired. It also supersedes, for the factory only, the rejection of a "pipeline of replaceable steps" in [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md); the `loop` library keeps plain scripts.

## Considered Options

- **A Stage kernel, with Workflow code routing on a closed outcome** — rejected. Each Workflow would still hand-write its loops, rework counters, hand-offs and resume checks around the kernel, so the repeated control flow and the duplicated recovery rules would stay.
- **Plain scripts over helper functions (the `loop` library's own model)** — rejected for the factory. That model suits a user who owns one procedure. The factory needs the same Stage in several Workflows, and a structural mistake (a Stage reading an Event nothing produces, a switch that skips a variant) should fail when the definition is built, not mid-run.
- **A general DAG engine** — rejected. Arbitrary graphs allow backward and forward jumps, make resume and tracing harder, and are more than the Workflows need. Four constructs are enough: sequence, per-item loop, exhaustive switch over a closed Event sum, and workspace scope.
- **A validated declarative runtime over a Stages library (chosen).**

## Decision

- A **Workflow definition** is an immutable value built with fluent calls; each call validates what it can see, so unknown, ambiguous or unproduced Event reads, a non-exhaustive switch, an invalid rework, hand-off, once-per-key or reroute are `DefinitionError`s at build time. `check()` validates the whole definition, `services()` derives the service types it needs and `explain()` prints which Stage feeds which.
- A **Stage** is an immutable value: typed input, the Event it produces, at most one executor (a role agent, a deterministic Action, or an Effect with a `reconcile`), a trusted gate returning pass, fail or hold, a repository contract and its Routes. Stages live in `software_factory/src/stages/` with complete defaults; Workflow-specific policy is passed as values or callables, never booleans.
- The **factory runtime** (`software_factory/src/runtime/`) is generic. `run(workflow, context, services=..., ledger=..., spans=...)` checks the definition and the explicitly passed `Services`, then interprets it, writing every decision to the append-only Execution ledger so an interrupted run resumes without repeating finished work.
- Layers: Workflow definitions over Flow fragments over Stages over Events over the runtime over role agents and shared infrastructure, enforced by import-linter and by structural tests that derive the rest from the code. Services are passed explicitly as a `Services` bundle; no module imports a dependency-injection container.
- The runtime's rules, constructs and escape hatches are recorded in [Factory Workflow Runtime](../concepts/str-factory-workflow-runtime.md).

## Consequences

- A new Workflow is a short definition plus a policy module; `address_once` was assembled from existing Stages with no new Stage logic.
- Rework, repository contracts (one validated commit, read-only, harness commit), hand-off, once-per-key and per-key limits, spans and crash recovery are implemented once and tested once; Stage tests need no Workflow.
- Authors learn a small definition language and its rules; a definition that is valid but expresses something the four constructs cannot needs one of the escape hatches in the concept, or a runtime change.
- The `workflows` extra and the Dishka dependency are gone; tests substitute fakes by passing `Services`.
- Console line format and telemetry names change from phase names to Stage ids ([Report factory runs as OpenTelemetry spans](report-factory-runs-as-opentelemetry-spans-bypassing-loops-phase-reporting.md)).
- The `loop` library is unchanged: it ships no workflows and no command.
