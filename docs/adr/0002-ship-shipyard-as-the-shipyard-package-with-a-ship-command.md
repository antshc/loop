# Ship Shipyard as the `shipyard` package with a single `ship` command

The Python orchestrator is being refactored into runtime-plus-workflows, and its names (`brain-tools` distribution, `afk` import package, `afk_dev`/`afk_fix_prs`/`afk_address_prs` scripts) predate the product name Shipyard. The package is named `shipyard` and exposes one console command, `ship`, whose subcommands are the discovered workflows (e.g. `ship dev --option <value>`).

## Considered Options

- **Keep the `afk` import package, add a `ralph` console command (`ralph dev`)** — rejected: Ralph names the loop pattern, not the tool; the product is Shipyard.
- **Keep `afk` and `afk dev`** — rejected: the name doesn't match the product Shipyard.
- **One console script per workflow (`afk_dev`, `afk_fix_prs`, `afk_address_prs`)** — rejected: the CLI must discover workflow commands as subcommands of one entry point, with no central registry.

## Consequences

- The import path, `pyproject.toml` project name, and console scripts all change; existing `afk_*` invocations in skills and docs must be migrated.
- ADR 0001's names (`brain-tools`, `afk_*`) are superseded by this naming; its distribution decision is unchanged.

See [CLI Runtime Workflow Architecture](../concepts/str-cli-runtime-workflow-architecture.md) for how workflows register subcommands.
