# Copilot Instructions

## 1. Scope and repository topology

Single repo: docs and codebase live together at the reporoot. There is no `workspace/` split and no `.harness.env`.

- **Docs & decisions:** `CONTEXT.md` (domain glossary) and `ARCHITECTURE.md` (ADR index, Concept index, and Codebase Structure), with ADRs under `docs/adr/` and Crosscutting Concepts under `docs/concepts/`.
- **Code:** `src/loop/` (the `loop` library, TypeScript consumed directly by Bun), `workflows/dev/` (self-contained dev Workflow with `main.ts`, named prompts, helpers, and tests), other examples under `workflows/`, and library tests under `tests/`. Loop is a library with no command. The layout is documented under **Codebase Structure** in `ARCHITECTURE.md`.
- **Archive:** `archive/` holds retired prototypes as a parts source only. It is excluded from editor search and file watching (see `.vscode/settings.json`) — do not navigate into it, edit it, or import from it.

Within this file, resolve conflicts in this order: **safety and repository targeting → authoritative sources → navigation → build and validation → documentation conventions.** This ordering scopes only the rules in this file; it does not override `AGENTS.md`, path-scoped instructions, or user instructions.

## 2. Safety and repository targeting

Before the first `git`/`gh` write action in a session (including `git worktree add`), run this check and stop if it fails:

```
test "$(git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')" = "antshc/loop"
```

Confirm the repo root with `git rev-parse --show-toplevel`. Origin: `antshc/loop`.

## 3. Authoritative sources

Consult these before searching the code:

- **Domain glossary:** [`CONTEXT.md`](../CONTEXT.md).
- **Architecture:** [`ARCHITECTURE.md`](../ARCHITECTURE.md) — ADR index, Concept index, and Codebase Structure.

## 4. Navigation policy

1. Read the relevant authoritative source first and state which doc you checked (or that none applies) before searching.
2. Use `ARCHITECTURE.md`'s Codebase Structure to scope searches to `src/loop/`, `workflows/`, or `tests/`.
3. Exact symbol (definition, references, rename) → language-server tools; concept or behavior → semantic search; literal or config value → text search.
4. Docs are leads, not proof — confirm behavior against current source or tests, then stop searching.

## 5. Build and validation policy

- Runtime: Bun with TypeScript for the library and example Workflows; preserve the minimum Bun declared in `package.json` (`engines`, `packageManager`). Node.js is unsupported.
- Install: `bun install` from the reporoot.
- Test: `bun run test` from the reporoot (lint, type-check, then `bun test`, configured by `package.json`, `eslint.config.js`, `tsconfig.json`, and `bunfig.toml`); it includes the dependency-cruiser architecture checks.
- For compatibility changes, follow [Test Strategy: Runtime compatibility](../docs/concepts/ops-test-strategy.md#runtime-compatibility); the latest local Bun alone does not verify the minimum.

## 6. Documentation conventions

- Author all docs (`CONTEXT.md`, `ARCHITECTURE.md`, ADRs, Crosscutting Concepts) at the reporoot.
- Keep `CONTEXT.md` and `ARCHITECTURE.md` high-level — no implementation details, specs, or scratch notes.
