# Copilot Instructions

## 1. Scope and repository topology

Single repo: docs and codebase live together at the reporoot. There is no `workspace/` split and no `.harness.env`.

- **Docs & decisions:** `GLOSSARY.md` (domain glossary) and `ARCHITECTURE.md` (ADR index, Concept index, and Codebase Structure), with ADRs under `docs/adr/` and Crosscutting Concepts under `docs/concepts/`.
- **Code:** `src/` (the `loop` library, including the Workflow runtime in `src/runtime/` and the shared Git and process helpers in `src/shared/`), library tests under `tests/`, the software factory under `workflows/software_factory/` (Stages, Events, Flow fragments, role agents, factory-only infrastructure and the `implement_spec`, `pr_babysit` and `address_once` Workflows), the standalone example under `workflows/loop/`, and Workflow tests under `workflows/tests/{loop,software_factory}/`. Loop is a library with no command. The layout is documented under **Codebase Structure** in `ARCHITECTURE.md`.
- **Archive:** `archive/` holds retired prototypes as a parts source only. It is excluded from editor search and file watching (see `.vscode/settings.json`) — do not navigate into it, edit it, or import from it.

Within this file, resolve conflicts in this order: **safety and repository targeting → authoritative sources → navigation → build and validation → documentation conventions.** This ordering scopes only the rules in this file; it does not override `AGENTS.md`, path-scoped instructions, or user instructions.

## 2. Safety and repository targeting

Before the first `git`/`gh` write action in a session (including `git worktree add`), run this check and stop if it fails:

```
python -c 'import re,subprocess,sys; url=subprocess.run(["git","remote","get-url","origin"],capture_output=True,text=True,check=True).stdout.strip(); slug=re.sub(r"\.git$","",re.sub(r"^(git@github\.com:|https://github\.com/)","",url)); sys.exit(0 if slug=="antshc/loop" else 1)'
```

Confirm the repo root with `git rev-parse --show-toplevel`. Origin: `antshc/loop`.

## 3. Authoritative sources

Consult these before searching the code:

- **Domain glossary:** [`GLOSSARY.md`](../GLOSSARY.md).
- **Architecture:** [`ARCHITECTURE.md`](../ARCHITECTURE.md) — ADR index, Concept index, and Codebase Structure.

## 4. Navigation policy

1. Read the relevant authoritative source first and state which doc you checked (or that none applies) before searching.
2. Use `ARCHITECTURE.md`'s Codebase Structure to scope searches to `src/`, `tests/`, `workflows/software_factory/`, `workflows/loop/`, or `workflows/tests/`.
3. Exact symbol (definition, references, rename) → language-server tools; concept or behavior → semantic search; literal or config value → text search.
4. Docs are leads, not proof — confirm behavior against current source or tests, then stop searching.

## 5. Build and validation policy

- Runtime: Python 3.14+ for the library and example Workflows; preserve the minimum declared in `pyproject.toml`.
- Install: `python -m pip install -e ".[dev]" --config-settings editable_mode=strict` from the reporoot.
- Style: run `python -m ruff check .` and `python -m ruff format --check .` from the reporoot before completing Python changes; both also run in the pull request gate.
- Test: `python -m pytest` from the reporoot (configured in `pyproject.toml`, `testpaths = ["tests", "workflows/tests"]`); it includes the import-linter architecture checks (`tests/test_architecture.py`).
- For compatibility changes, follow [Test Strategy: Python compatibility](../docs/concepts/ops-test-strategy.md#python-compatibility); a newer local interpreter alone does not verify the minimum.

## 6. Documentation conventions

- Author all docs (`GLOSSARY.md`, `ARCHITECTURE.md`, ADRs, Crosscutting Concepts) at the reporoot.
- Keep `GLOSSARY.md` and `ARCHITECTURE.md` high-level — no implementation details, specs, or scratch notes.
