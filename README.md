<p align="left">
  <img src="loop_icon.png" alt="Loop" width="128" height="128">
</p>

# Loop

Python library for composing autonomous agent Workflows on Git worktrees.

Loop ships no command and no built-in Workflows. You write each Workflow as a plain Python script on the public `loop` API and run it through your own shell alias. The repository's [`workflows/dev/`](workflows/dev/) package is an example only.

## Features

- **Agent builder** — `Agent()` composes git, Docker, session, and hook options into agent clients and Git worktrees; agents run on the host with the worktree as working directory.
- **Agent profiles** — Copilot CLI and Codex adapters with per-client model and reasoning effort, session resume, and raw stdout as the result.
- **Branch strategies** — worktrees on a named branch, a merged temporary branch, or the repository head; the example Workflows add a GitHub client for draft pull requests and Ticket state, where Python owns push, pull requests, and Ticket state and the agent commits each task.
- **Loop hooks** — shell commands run on the host at `worktree-ready`, `worktree-removing`, and `run-finished`.
- **Dry run** — `python -m loop --dry-run` exercises the builder with logging-only adapters.

## Requirements

- Python 3.12 or newer
- `git`
- GitHub CLI (`gh`) for the GitHub client
- Copilot CLI (or Codex CLI) for the agent client

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
from loop import Agent, AgentProfile, AgentRequest, BranchStrategy, GitOptions, copilot

options = GitOptions(strategy=BranchStrategy("fix-tests", "main"))
with Agent().with_git(options).open() as worktree:
    agent = worktree.agent(AgentProfile(copilot, "claude-sonnet-5.5", "high"))
    result = agent.run(AgentRequest("Fix the failing tests and commit."))
    print(result.output)
```

Run it through your own alias:

```sh
alias dev='PYTHONPATH=/path/to/harness python -m workflows.dev'
dev --log-level DEBUG
```

See [`workflows/dev/`](workflows/dev/) for a complete Workflow, [`workflows/plan_implement.py`](workflows/plan_implement.py) for two runs with different models on one worktree, and [`workflows/dev/prompts/dev.md`](workflows/dev/prompts/dev.md) for the `dev` prompt template.

## Repository layout

| Location | Contents |
|---|---|
| `src/loop/` | The `loop` library |
| `workflows/` | Example `dev` and `plan_implement` Workflows and the `dev` prompt template |
| `tests/` | Test suite: `tests/unit/` (unit tests against fakes), `tests/workflow_harness.py` (shared Workflow harness), and import-linter architecture checks |
| `docs/` | ADRs, Crosscutting Concepts, and research notes |
| `archive/` | Retired prototypes; parts source only |

## Documentation

- [CONTEXT.md](CONTEXT.md) — domain glossary
- [ARCHITECTURE.md](ARCHITECTURE.md) — building blocks, ADRs, and Crosscutting Concepts
- [System context](docs/building-blocks/system-context.md) — context and container diagrams
