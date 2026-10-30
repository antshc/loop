# Compose Workflows as declarative definitions over a Stages library, run by Loop's Workflow runtime

`implement_spec` and `pr_babysit` were vertical slices: feature packages of classes wired by a Dishka composition root, each with its own control flow, its own hand-kept list of phases, and its own copy of the same rework, commit-validation, hand-off and recovery logic. A third Workflow (`address_once`) would have copied the babysit address path again, and no slice could say what an interrupted run had already done. Each Workflow is now one **declarative definition** over a library of **Stages**, executed by a generic **Workflow runtime**. The runtime and the typed agent contract its Stages run are part of the `loop` library (`loop.runtime`, exported from top-level `loop`); the factory's Stages, Events, Flow fragments, role agents and policy stay in `workflows/software_factory/`. This supersedes [Structure workflows as vertical slices over a shared package, wired by a Dishka composition root](structure-workflows-as-vertical-slices-over-a-shared-package-wired-by-dishka.md), and the library's plain-script model: the runtime is Loop's one Workflow architecture. Applies to every Workflow built on Loop.

## Considered Options

- **A Stage kernel, with Workflow code routing on a closed outcome** — rejected. Each Workflow would still hand-write its loops, rework counters, hand-offs and resume checks around the kernel, so the repeated control flow and the duplicated recovery rules would stay.
- **Plain scripts over helper functions (the library's earlier model)** — rejected. That model suits a user who owns one procedure. Workflows need the same Stage in several places, and a structural mistake (a Stage reading an Event nothing produces, a switch that skips a variant) should fail when the definition is built, not mid-run.
- **A general DAG engine** — rejected. Arbitrary graphs allow backward and forward jumps, make resume and tracing harder, and are more than the Workflows need. Four constructs are enough: sequence, per-item loop, exhaustive switch over a closed Event sum, and workspace scope.
- **A factory-only runtime in the factory's own package, beside a library that kept plain scripts** (the previous decision) — rejected: two Workflow architectures, and a runtime that knows no GitHub, Ticket or process was already generic library code.
- **Promote only the runtime's contracts and abstract types into Loop, keeping execution and the ledger factory-owned** — rejected: definitions, validation and execution are only reusable together.
- **A validated declarative runtime in Loop over a Workflow-owned Stages library (chosen).**

## Decision

- A **Workflow definition** is an immutable value built with fluent calls; each call validates what it can see, so unknown, ambiguous or unproduced Event reads, a non-exhaustive switch, an invalid rework, hand-off, once-per-key or reroute are `DefinitionError`s at build time. `check()` validates the whole definition, `services()` derives the service types it needs and `explain()` prints which Stage feeds which.
- A **Stage** is an immutable value: typed input, the Event it produces, at most one executor (a typed agent, a deterministic Action, or an Effect with a `reconcile`), a trusted gate returning pass, fail or hold, a repository contract and its Routes. The factory's Stages live in `workflows/software_factory/stages/` with complete defaults; Workflow-specific policy is passed as values or callables, never booleans.
- The **runtime** (`src/runtime/`) is generic: it never imports a Workflow, the shared Git, process or GitHub helpers, or `subprocess`. `run(workflow, context, services=..., ledger=..., spans=...)` checks the definition and the explicitly passed `Services`, then interprets it, writing every decision to the append-only Execution ledger so an interrupted run resumes without repeating finished work. Its engine stays private to the runtime package.
- Layers: Workflow definitions over Flow fragments over Stages over Events over role agents and Workflow infrastructure, all over Loop, enforced by import-linter and by structural tests that derive the rest from the code. Services are passed explicitly as a `Services` bundle; no module imports a dependency-injection container.
- The ledger stores each value under its class's module-qualified name. Moving a stored class breaks earlier ledgers on purpose: resuming one raises an explicit decode error naming the type; there is no translation or migration, and old state is removed by the user.
- The runtime's rules, constructs and escape hatches are recorded in [Factory Workflow Runtime](../concepts/str-factory-workflow-runtime.md).

## Consequences

- A new Workflow is a short definition plus a policy module; `address_once` was assembled from existing Stages with no new Stage logic, and the `plan_implement` example uses its own example-owned Stages.
- Rework, repository contracts (one validated commit, read-only, harness commit), hand-off, once-per-key and per-key limits, spans and crash recovery are implemented once and tested once in the library; Stage tests need no Workflow.
- Authors learn a small definition language and its rules; a definition that is valid but expresses something the four constructs cannot needs one of the escape hatches in the concept, or a runtime change.
- The `workflows` extra and the Dishka dependency are gone; tests substitute fakes by passing `Services`.
- Console line format and telemetry names are Stage ids ([Report Workflow runs as runtime OpenTelemetry spans](report-factory-runs-as-opentelemetry-spans-bypassing-loops-phase-reporting.md)).
