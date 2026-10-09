<p align="left">
  <img src="loop_icon.png" alt="Loop" width="128" height="128">
</p>

# Loop

Python library for composing autonomous agent Workflows on Git worktrees.

Loop ships no command and no built-in Workflows. You write each Workflow as a plain Python script on the public `loop` API and run it through your own shell alias. The repository's [`workflows/dev/`](workflows/dev/) package is an example only.

## Features

- **Agent runner** — creates a Git worktree, binds one agent client to it, and runs prompts on the host from the harness root, collecting each run's commits.
- **Agent clients** — Copilot CLI client with live output streaming, per-run model and reasoning effort, and session resume.
- **Git services** — branch, worktree, and commit services; the example Workflows use a GitHub client for draft pull requests and Ticket state, where Python owns push, pull requests, and Ticket state and the agent commits each task.
- **Lifecycle hooks** — shell-command hooks run on the host before the agent starts.
- **Stores** — file and in-memory stores for sessions and executions.
- **Test doubles** — fakes for `gh`, `git`, and the Copilot CLI in `loop.testing`.

## Requirements

- Python 3.12 or newer
- `git`
- GitHub CLI (`gh`) for the GitHub client
- Copilot CLI for the Copilot agent client

## Install

Loop is installed with `pip` from this repository ([Install Loop with pip](docs/adr/install-loop-with-pip-from-the-repository.md)).

```sh
pip install -e .
```

For development:

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # once
pytest
```

To run the example `dev` Workflow on this repository:

```sh
gh auth status               # needs an authenticated gh with access to antshc/loop
copilot --version            # the default agent client is Copilot CLI
python -m workflows.dev
```

## Usage

Write a Workflow as a script that imports only from `loop`:

```python
from pathlib import Path

from loop import AgentRunnerProvider, Git, InMemorySessionStore, Prompt, RepositoryData, copilot

harness_root = Path.cwd()
repository = RepositoryData(
    path=harness_root,
    owner_repo="owner/my-repo",
    is_harness=True,
    worktree_root=harness_root / "workspace" / f"{harness_root.name}.worktrees",
)
provider = AgentRunnerProvider(Git(), harness_root, copilot(InMemorySessionStore()))

with provider.create(repository, base="main") as runner:
    run = runner.run(Prompt("Fix the failing tests and commit."), "claude-sonnet-5.5", "high")
    print(run.result.response, run.commits)
```

Run it through your own alias:

```sh
alias dev='PYTHONPATH=/path/to/harness python -m workflows.dev'
dev --harness-root . --log-level DEBUG
```

See [`workflows/dev/`](workflows/dev/) for a complete Workflow, [`workflows/plan_implement.py`](workflows/plan_implement.py) for two runs with different models on one worktree, and [`workflows/dev/prompts/dev.md`](workflows/dev/prompts/dev.md) for the `dev` prompt template.

## Repository layout

| Location | Contents |
|---|---|
| `src/loop/` | The `loop` library |
| `src/loop/testing/` | Test doubles for every process boundary |
| `workflows/` | Example `dev` and `plan_implement` Workflows and the `dev` prompt template |
| `tests/` | Test suite: `tests/unit/` (unit group), `tests/integration/` (integration group), `tests/workflow_harness.py` (shared Workflow harness), and import-linter architecture checks |
| `docs/` | ADRs, Crosscutting Concepts, and research notes |
| `archive/` | Retired prototypes; parts source only |

## Documentation

- [CONTEXT.md](CONTEXT.md) — domain glossary
- [ARCHITECTURE.md](ARCHITECTURE.md) — building blocks, ADRs, and Crosscutting Concepts
- [System context](docs/building-blocks/system-context.md) — context and container diagrams
