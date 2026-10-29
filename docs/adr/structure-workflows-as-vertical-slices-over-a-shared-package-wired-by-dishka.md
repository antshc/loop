# Structure workflows as vertical slices over a shared package, wired by a Dishka composition root

`implement_spec` and `pr_babysit` had grown into flat module piles. Each duplicated the same Git, `gh`, subprocess, JSON-state, envelope, and telemetry helpers, and each wired its dependencies through long keyword-default parameter lists on `main`. Each Workflow is now a **vertical slice**: one package per feature (implement_spec: `plan`, `deliver`, `chore`, `review`, `backlog`; babysit: `discover`, `monitor`, `watch`, `address`, `maintain_ci`, `publish`), plus `model`, `agents`, and one `composition` package. Infrastructure whose meaning is identical in both moved into `workflows/shared/` (`git`, `github`, `process`, `state`, `telemetry`, `agents`, `providers`). Features are classes that receive collaborators through their constructors. Only `composition/` knows the container: it builds a [Dishka](https://dishka.readthedocs.io/) container from `SharedProvider`, the slice's own provider, and any caller-supplied override providers. `main(argv=None, *, providers=())` replaces the keyword seams. APP scope is the process; REQUEST scope is one unit of work, either one implement_spec Workflow run or one babysat pull request.

## Considered Options

- **Keep keyword-default seams on `main`** — rejected. Each new collaborator widened `main`'s signature, and wiring order was hidden in a hand-written function that tests had to mirror.
- **Hand-written composition functions without a container** — rejected. That approach works, but it re-implements scoping (process versus per-run objects) and override-by-type for tests, which Dishka provides directly.
- **`dependency-injector` or `punq`** — rejected. Dishka registers by type (including `NewType` keys such as `Harness`), has explicit scopes, and lets later providers override earlier ones, which matches how tests inject fakes.
- **Duplicate infrastructure per Workflow** (the earlier rule) — rejected. The two copies had already drifted, and each Workflow still needs only one copy of `shared/`.
- **Horizontal layers (`domain/`, `infrastructure/`, `application/`)** — rejected. The layers stayed mostly empty, and one feature change touched every layer.

## Consequences

- Running `implement_spec` or `pr_babysit` needs the `workflows` extra (`pip install -e ".[workflows]"`). The `loop` library itself takes no Dishka dependency.
- Copying a Workflow to another harness means copying its slice and `workflows/shared/` together. No Workflow imports a sibling Workflow.
- Tests and user scripts replace any dependency by passing a provider to `main`. `workflows.shared.providers.bind` binds constants, and implement_spec's `use_repositories(...)` replaces the configured repository targets.
- Import-linter contracts enforce the following:
  - `shared` imports no slice.
  - Only composition roots and `shared.providers` import `dishka`.
  - Features never import their composition root.
  - `implement_spec` stages are independent of each other and of `agents`.
- Constructor injection makes feature classes testable without a container. The cost is one more indirection when reading how an object is built.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how it is applied.
