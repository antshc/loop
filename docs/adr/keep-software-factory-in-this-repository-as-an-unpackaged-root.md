# Keep the software factory in this repository under `workflows/`, outside the library-only `loop` wheel

The factory first lived in `software_factory/` beside a nested `loop/` project, with its own `src/`, `workflows/` and `tests/` mapped into three package roots (`loop`, `software_factory`, `factory_workflows`) that one editable install exposed. Once Loop took over the generic Workflow runtime, the typed agent contract and the Git, process and GitHub helpers, the factory had only Workflow-specific code left. The repository now holds the library in `src/` (package `loop`, mapped by `package-dir`), its tests in `tests/`, and every repository-run Workflow under `workflows/`: the factory in `workflows/software_factory/` (Stages, Events, Flow fragments, role agents, factory-only infrastructure and the `implement_spec`, `pr_babysit` and `address_once` packages), its tests in `workflows/tests/software_factory/`, and the standalone example in `workflows/loop/`. Applies to this repository's factory and example Workflows.

## Considered Options

- **A new repository for the factory** (the reusable-agents design's assumption) — rejected: it splits the factory from the library it exercises, so a library change and the factory change that needs it would be two changes in two repositories.
- **Ship the factory inside the `loop` wheel** — rejected: [Ship Loop as a workflow library](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md) says the library ships no workflows, and every installer of `loop` would receive GitHub and Ticket policy.
- **A second installable distribution in this repository** — rejected: it adds packaging, versioning and a release path no one consumes; the factory is run from a clone.
- **A `software_factory/` package root beside `loop/`, mapped by `package-dir` into `software_factory` and `factory_workflows`** (the previous decision) — rejected: the factory's package list became part of the installed artifact, two namespaces named one factory, and the runtime and shared helpers it held were reusable library code.
- **Compatibility shims for the old `software_factory` and `factory_workflows` imports and commands, or a translation of old ledger type names** — rejected: nothing outside this repository depends on them; the user removes pre-refactor state manually.
- **Repository-run Workflows under `workflows/` with a library-only wheel (chosen).**

## Decision

- The installed artifact is the `loop` library only: no Workflow, prompt, settings file or test is packaged.
- Factory code imports as `workflows.software_factory`; users run `python -m workflows.software_factory.<workflow>` (and `python -m workflows.loop.plan_implement`) from the repository root. There are no old-namespace modules or command aliases.
- Factory and example code imports only the public `loop` package and its public `loop.shared` modules; import-linter contracts and the structural tests in `workflows/tests/software_factory/architecture/` enforce the factory's layering in the same `pytest` run as the library's.
- Execution ledgers written before the move name types by their old modules and fail to decode with an explicit error; they are neither migrated nor deleted.

## Consequences

- One repository revision defines the library and the Workflows that exercise it; a library change and the Workflow change that needs it land together.
- Reusing the factory elsewhere means copying `workflows/software_factory/`; nothing in `loop` imports it.
- The `loop` extras describe the library only; the factory needs `loop[otel]` only for its OpenTelemetry export.
