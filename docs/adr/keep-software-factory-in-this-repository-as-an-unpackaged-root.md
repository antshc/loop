# Keep `software_factory` in this repository as an unpackaged root

The cross-Workflow reusable agents design ([Option E](../design/cross-workflow-reusable-agents.md)) assumed the factory would move to a new repository with its own package. The factory is instead a flat package, `software_factory/`, at this repository's root, outside the `loop` distribution.

## Considered Options

- **A new repository for the factory** (the design's assumption) — rejected for now: it splits the factory from the library it exercises, so a library change and the factory change that needs it would be two changes in two repositories.
- **Ship the factory inside the `loop` wheel** — rejected: [Ship Loop as a workflow library](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md) says the library ships no workflows, and every installer of `loop` would receive GitHub and Ticket policy.
- **A second installable distribution in this repository** — rejected: it adds packaging, versioning and a release path no one consumes; the factory is run from a clone.
- **An unpackaged root package in this repository (chosen).**

## Decision

- `software_factory/` sits at the reporoot, beside `loop/src/loop/`. `packages.find` searches only `loop/src`, so the `loop` wheel never contains it, and a test asserts that.
- It imports only the public top-level `loop` package. An import-linter contract forbids every `loop.*` submodule, and `software_factory` is a root package of the architecture checks, so the layering of the factory is enforced in the same `pytest` run as the library's.
- Users run its Workflows from a clone with `python -m software_factory.workflows.<name>`, from the harness root; its tests live in `software_factory/tests/` and are discovered by the root `pytest`.
- Standalone public-Loop examples (`plan_implement.py`, `workflow_reporting.py`) stay under `loop/workflows/`.

## Consequences

- One repository revision defines the library and the factory that exercises it; a library change and the factory change that needs it land together.
- The factory is not installable as a dependency. Reusing it elsewhere means copying the package or vendoring the repository; moving it to its own repository later means moving one directory and its tests, because nothing in `loop` imports it.
- The `loop` extras describe the library only; the factory needs `loop[otel]` only for its OpenTelemetry export.
