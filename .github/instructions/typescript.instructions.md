---
description: TypeScript code style and conventions for this repo — compiler and lint gates, naming, value objects, errors, async, and scope discipline.
applyTo: "**/*.ts"
---

# TypeScript code style

`bun run test` runs ESLint, `tsc --noEmit`, then `bun test`; a violation fails the build. The gates win over this file; confirm each rule against neighboring files before trusting it over what the codebase does.

## Runtime and gates

- MUST target Bun only; source is consumed directly with no build step. Preserve the minimum Bun in `package.json`.
- MUST pass `strict` plus `noUncheckedIndexedAccess`, `noImplicitOverride`, `noFallthroughCasesInSwitch`, `verbatimModuleSyntax`; `allowImportingTsExtensions` and `noEmit` stay on.
- MUST pass ESLint `strictTypeChecked`, `stylisticTypeChecked`, and `perfectionist` `recommended-natural` with no rule overrides.
- MUST NOT write `eslint-disable`, `@ts-ignore`, or `@ts-expect-error`; `noInlineConfig` is on and unused directives fail. Fix the code.
- MUST NOT add a dependency unless the task needs it and the codebase does not already supply it.

## Imports and sorting

- MUST import with the `.ts` extension; use `import type` for type-only imports.
- MUST keep imports, named specifiers, object keys, interface members, and class members in the order `perfectionist` natural sorting enforces; run `eslint --fix` rather than sorting by hand.
- Workflows MUST import Loop only from the public entry point `src/loop/index.ts`; only `src/loop/observe/otel.ts` imports `@opentelemetry/*` (dependency-cruiser fails the suite otherwise).

## Naming and types

- MUST use `camelCase` for functions, variables, and members, and `PascalCase` for classes, interfaces, type aliases, and `Error` subclasses; follow neighboring files for module constants (`UPPER_SNAKE_CASE` for fixed settings).
- MUST model string enums as `as const` objects with a same-named union type, never `enum`.
- MUST type every parameter explicitly.
- MUST pass optional configuration as a trailing options object (`new GitOptions({ ... })`), not positional flags or a long parameter list.
- MUST make validated value objects `readonly` classes that validate in the constructor, throw `ValueError` on bad input, and `Object.freeze(this)`.
- MUST use interfaces plus type guards for seams that code duck-checks at runtime (such as an optional `span()` capability).
- Prefer `#private` fields and `readonly` members; prefer `unknown` plus a narrowing guard over `any` or type assertions.

## Functions and structure

- MUST keep all statements inside a function at the same level of abstraction; extract lower-level detail into named helpers.
- MUST make every operation that starts a process or does I/O `async`; keep builders, profiles, and value objects synchronous.
- MUST open worktrees, runs, and phases as callback scopes that run a body and return its result; MUST NOT use `await using` for them.
- SHOULD accept injectable dependencies (`now`, `clock`, a runner, an output stream) as optional members of a trailing options or dependencies object for testability.
- Workflows MUST wire dependencies through their composition root (`Symbol.for` tokens, constructor injection) and accept overrides in `main(argv, dependencies)`; MUST NOT export the container from Loop's public API.

## Errors

- MUST verify the actual error a call throws or rejects with before writing `catch`; narrow with `instanceof` rather than assuming the "obviously named" class.
- MUST define errors as `Error` subclasses that set `this.name`, accept `ErrorOptions`, and pass `cause` when wrapping.
- MUST rethrow the original error when a cleanup step fails while an error propagates; log the cleanup failure and attach it with `addNote`.
- MUST NOT use an empty `catch` or a blanket `catch` for control flow or retry.

## Output, logging, and processes

- MUST write user-facing errors to stderr (`process.stderr` or the injected stream).
- SHOULD reuse the module's `getLogger` pattern for diagnostic detail.
- MUST start agent child processes through Loop's process runner (`src/loop/run/process.ts`) so SIGINT and SIGTERM end the whole process group.

## Comments

- MUST comment non-obvious intent the code cannot show: why, invariants, business rules, cross-boundary effects.
- MUST keep each comment to one short line; place it on the line above the statement or branch it explains.
- MUST document every public contract (exported interface, class, function) with a one-line `/** */` stating its responsibility.
- MUST NOT add comments that restate the next line.

## Tests

- MUST use `bun:test`; drive behavior through public seams with injected doubles, one behavior per test, named as a sentence.
- MUST create a per-test temporary directory and remove it afterwards; MUST NOT touch user repositories or the network.

## Scope discipline

- MUST NOT refactor, rename, or reformat code outside the lines a task requires changing.
- MUST NOT change persisted or emitted strings (hook points, state files, log records, span and metric names).
