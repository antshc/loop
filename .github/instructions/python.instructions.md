---
description: Python code style and conventions for this repo — typing, naming, exceptions, logging, and scope discipline.
applyTo: "**/*.py"
---

# Python code style

Confirm each rule below against neighboring files in the same package before trusting it over what the codebase actually does — the codebase wins when it conflicts.

## Typing & imports

- MUST start every module with `from __future__ import annotations`.
- MUST use PEP 604/585 hints (`str | None`, `list[str]`, `dict[str, Any]`); MUST NOT use `typing.Optional`/`List`/`Dict`.
- SHOULD keep imports at module scope unless the file already uses lazy in-function imports for an expensive/optional dependency.

## Naming & structure

- MUST use `snake_case` for functions/variables, `PascalCase` for classes.
- MUST use `@dataclass(frozen=True)` for simple immutable value objects.
- MUST use f-strings for interpolation.
- SHOULD prefer `pathlib.Path` over `os.path`.
- SHOULD accept injectable dependencies (`now`, `sleep`, a client) as optional keyword-only params for testability.

## Exceptions — the #1 recurring bug class

- MUST verify the actual exception type a call raises (library source/docs) before writing `except` — never assume the "obviously named" class is the one thrown.
- MUST name caught exception variables `exception`.
- MUST NOT use bare `except:` or blanket `except Exception:` for control-flow/retry logic.

## Output & logging

- MUST print user-facing errors to `sys.stderr`.
- SHOULD reuse the module's existing `logging` pattern for diagnostic detail when one already exists.

## Scope discipline

- MUST NOT add docstrings/comments that restate the next line.
- MUST NOT refactor, rename, or reformat code outside the lines a task requires changing.
- MUST NOT add a new dependency unless the task needs it and it's already used elsewhere in the codebase.
