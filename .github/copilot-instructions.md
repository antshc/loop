# Copilot Instructions

## 1. Scope and repository topology

Single repo: docs and codebase live together at the reporoot. There is no `workspace/` split and no `.harness.env`.

- **Docs & decisions:** `CONTEXT.md` (domain glossary) and `ARCHITECTURE.md` (Concept index and Codebase Structure), with Crosscutting Concepts under `docs/concepts/`. Standalone ADRs may live under `docs/adr/`; `ARCHITECTURE.md` does not index them.
- **Code:** `ralph/` (the `ralph` plugin and its skills) and `tools/` (Python package `brain-tools`: `tools/src/afk`, `tools/src/modules`, `tools/tests`). The source hierarchy is documented under **Codebase Structure** in `ARCHITECTURE.md`.

Within this file, resolve conflicts in this order: **safety and repository targeting → authoritative sources → navigation → build and validation → documentation conventions.** This ordering scopes only the rules in this file; it does not override `AGENTS.md`, path-scoped instructions, or user instructions.

## 2. Safety and repository targeting

Before the first `git`/`gh` write action in a session (including `git worktree add`), run this check and stop if it fails:

```
python -c 'import re,subprocess,sys; url=subprocess.run(["git","remote","get-url","origin"],capture_output=True,text=True,check=True).stdout.strip(); slug=re.sub(r"\.git$","",re.sub(r"^(git@github\.com:|https://github\.com/)","",url)); sys.exit(0 if slug=="antshc/ralphv2" else 1)'
```

Confirm the repo root with `git rev-parse --show-toplevel`. Origin: `antshc/ralphv2`.

## 3. Authoritative sources

Consult these before searching the code:

- **Domain glossary:** [`CONTEXT.md`](../CONTEXT.md).
- **Architecture:** [`ARCHITECTURE.md`](../ARCHITECTURE.md) — Concept index and Codebase Structure.
- **Skills overview:** [`ralph/skills/README.md`](../ralph/skills/README.md).

## 4. Navigation policy

1. Read the relevant authoritative source first and state which doc you checked (or that none applies) before searching.
2. Use `ARCHITECTURE.md`'s Codebase Structure to scope searches to `ralph/` or `tools/`.
3. Exact symbol (definition, references, rename) → language-server tools; concept or behavior → semantic search; literal or config value → text search.
4. Docs are leads, not proof — confirm behavior against current source or tests, then stop searching.

## 5. Build and validation policy

- Install: `pip install -e ".[dev]"` from the reporoot.
- Test: `pytest` (configured in `pyproject.toml`, `testpaths = ["tools/tests"]`).

## 6. Documentation conventions

- Author all docs (`CONTEXT.md`, `ARCHITECTURE.md`, ADRs, Crosscutting Concepts) at the reporoot.
- Keep `CONTEXT.md` and `ARCHITECTURE.md` high-level — no implementation details, specs, or scratch notes.

## 7. Skills

- Plugin skills live under `ralph/skills/<skill-name>/` (`SKILL.md` plus any supporting files).
- Do NOT reference a skill's own files via bare relative markdown links; they resolve against the runtime CWD and can silently fail. Use the skill's absolute base directory.
- Available skills: `address`, `create-worktree`, `delete-worktree`, `dev`, `fix`, `init-harness`, `ralph-build` — see each `SKILL.md` frontmatter for trigger conditions; do not restate them here.
