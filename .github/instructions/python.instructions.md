---
description: Python code style and conventions for this repo — typing, naming, exceptions, logging, and scope discipline.
applyTo: "**/*.py"
---

# Python code style

Confirm each rule below against neighboring files in the same package before trusting it over what the codebase actually does — the codebase wins when it conflicts.

## Automated style checks

- MUST follow PEP 8 naming and spacing, with Ruff formatting at 88 columns and the lint rules configured in `pyproject.toml`.
- Install the pinned Ruff version with the development dependencies: `python -m pip install -e ".[dev]" --config-settings editable_mode=strict`.
- MUST run `python -m ruff check .` and `python -m ruff format --check .` from the repository root before completing Python changes, even if skipping them would be faster; the pull request gate runs the same commands to prevent style drift.
- Fix supported lint issues with `python -m ruff check . --fix`, then format with `python -m ruff format .`; review the diff and run the checks and `python -m pytest` again.
- Keep `archive/` excluded; do not suppress checks or expand exclusions to hide failures in active code.

## Runtime compatibility

- MUST keep `src/`, `tests/`, `workflows/` (including `workflows/tests/`) compatible with Python 3.14; `pyproject.toml` declares the supported minimum.
- MAY use any 3.14 syntax and standard-library API (`StrEnum`, `Exception.add_note`, variable-fraction `datetime.fromisoformat`); no fallbacks for older runtimes.
- Validate compatibility changes on the minimum supported runtime per [Test Strategy](../../docs/concepts/ops-test-strategy.md#python-compatibility).

## Typing & imports

- MUST start every module with `from __future__ import annotations`.
- MUST use PEP 604/585 hints (`str | None`, `list[str]`, `dict[str, Any]`); MUST NOT use `typing.Optional`/`List`/`Dict`.
- SHOULD keep imports at module scope unless the file already uses lazy in-function imports for an expensive/optional dependency.

## Naming & structure

- MUST use `snake_case` for functions/variables, `PascalCase` for classes.
- MUST use `@dataclass(frozen=True)` for simple immutable value objects.
- MUST use f-strings for interpolation.
- MUST keep all statements inside a function at the same level of abstraction; extract lower-level detail into named helpers.
- SHOULD prefer `pathlib.Path` over `os.path`.
- SHOULD accept injectable dependencies (`now`, `sleep`, a client) as optional keyword-only params for testability.

## Exceptions — the #1 recurring bug class

- MUST verify the actual exception type a call raises (library source/docs) before writing `except` — never assume the "obviously named" class is the one thrown.
- MUST name caught exception variables `exception`.
- MUST NOT use bare `except:` or blanket `except Exception:` for control-flow/retry logic.

## Output & logging

- MUST print user-facing errors to `sys.stderr`.
- SHOULD reuse the module's existing `logging` pattern for diagnostic detail when one already exists.

## Comments

- MUST comment non-obvious intent the code cannot show: why, invariants, business rules, cross-boundary effects.
- MUST keep each comment to one short line; no multi-paragraph docstrings where one line suffices.
- MUST document every public contract (ABC, public API class, function, or method) with a one-line docstring stating its responsibility.
- Place the comment on the line above the statement or branch it explains.

## Scope discipline

- MUST NOT add docstrings/comments that restate the next line.
- MUST NOT refactor, rename, or reformat code outside the lines a task requires changing.
- MUST NOT add a new dependency unless the task needs it and it's already used elsewhere in the codebase.
