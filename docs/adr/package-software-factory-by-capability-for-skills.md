# Package the software factory by capability for installable skills

Status: Accepted. Supersedes [Keep the software factory under workflows](keep-software-factory-in-this-repository-as-an-unpackaged-root.md) for the factory source location.

## Decision

- Keep the factory in this repository and outside the installed `loop` wheel.
- Use the standalone `software_factory` Python package, with `core`, `pull_requests`, `spec_delivery`, and runnable `workflows` subpackages.
- Keep one import path in the repository and generated skill: `software_factory.*`.
- Generate the babysit skill's `scripts/software_factory/` from canonical `core`, `pull_requests`, and `workflows/pr_babysit` sources. Never edit the generated copy.
- Preserve previously persisted factory and GitHub type tags through the factory ledger's read-only relocation mapping, even when simpler to discard old records.

## Rationale

- A skill installs without the factory repository checkout, and needs no `_bundled/workflows/` wrapper or import rewriting.
- PR maintenance and spec delivery have distinct dependencies. The babysit skill must not copy spec implementation agents and stages.
- `core` owns shared types and GitHub primitives; capability packages own their specific behavior.
- The Loop library stays reusable, without factory dependencies or bundled workflow code.

## Consequences

- Run factory workflows with `python3 -m software_factory.workflows.<name>` from the repository root.
- Install the babysit skill's Python requirements separately, including `loop[otel]`.
- Keep tests for the standalone copied bundle, canonical/generated drift, workflow portability, and ledger recovery.
