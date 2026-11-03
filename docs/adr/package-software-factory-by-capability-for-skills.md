# Package the software factory by capability for installable skills

Status: Amended: shared core now ships in `loop.software_factory`. Supersedes [Keep the software factory under workflows](keep-software-factory-in-this-repository-as-an-unpackaged-root.md) for the factory source location.

## Decision

- Ship shared factory core from `src/software_factory/` as `loop.software_factory` in the `loop` wheel; keep capability workflows outside the wheel.
- Use the standalone `software_factory` Python package, with `pull_requests`, `spec_delivery`, runnable `workflows`, and `github.py` for capability composition.
- Keep one import path in the repository and generated skill: `software_factory.*`.
- Generate the babysit skill's `scripts/software_factory/` from canonical `pull_requests`, `github.py`, and `workflows/pr_babysit` sources; obtain shared helpers from the installed Loop requirement. Never edit the generated copy.
- Preserve previously persisted factory and GitHub type tags through the factory ledger's read-only relocation mapping, even when simpler to discard old records.

## Rationale

- A skill installs without the factory repository checkout, and needs no `_bundled/workflows/` wrapper or import rewriting.
- PR maintenance and spec delivery have distinct dependencies. The babysit skill must not copy spec implementation agents and stages.
- `loop.software_factory` owns shared types and GitHub transport. PR recovery worktrees, CI-aware verdicts and GitHub capability composition stay in the repository package.
- The Loop library includes reusable factory helpers and no built-in workflow definitions. Public agent helpers replace the cross-package private `_kit` imports; optional telemetry stays behind `loop[otel]`.

## Consequences

- Run factory workflows with `python3 -m software_factory.workflows.<name>` from the repository root.
- Install the babysit skill's Python requirements separately, including `loop[otel]`.
- Keep tests for the standalone copied bundle, canonical/generated drift, workflow portability, and ledger recovery.
