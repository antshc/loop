# Ship Loop as the `loop` package with a single `loop` command

**Status:** Superseded in part — the `loop` package name stands; the single `loop` console command does not. Loop ships no command: users register their own aliases for Workflow scripts ([ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md), spec #34).

The Python CLI is being refactored into runtime-plus-workflows, and its names (`brain-tools` distribution, `afk` import package, `afk_dev`/`afk_fix_prs`/`afk_address_prs` scripts) predate the product name Loop. The package is named `loop` and exposes one console command, `loop`, whose subcommands are the discovered workflows (e.g. `loop dev --option <value>`).

## Considered Options

- **Keep the `afk` import package, add a `ralph` console command (`ralph dev`)** — rejected: Ralph is the orchestrator, not the CLI; the CLI and top-level product is Loop.
- **Keep `afk` and `afk dev`** — rejected: the name doesn't match the product Loop.
- **One console script per workflow (`afk_dev`, `afk_fix_prs`, `afk_address_prs`)** — rejected: the CLI must discover workflow commands as subcommands of one entry point, with no central registry.

## Consequences

- The import path, `pyproject.toml` project name, and console scripts all change; existing `afk_*` invocations in skills and docs must be migrated.
- ADR 0001's names (`brain-tools`, `afk_*`) are superseded by this naming; its distribution decision is unchanged.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how workflows are run today.
