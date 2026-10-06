<p align="left">
  <img src="loop_icon.png" alt="Loop" width="128" height="128">
</p>

# Loop

Python library for composing autonomous agent Workflows on Git worktrees and Sandboxes.

Loop ships no command and no built-in Workflows. You write each Workflow as a plain Python script on the public `loop` API and run it through your own shell alias. The repository's [`workflows/dev.py`](workflows/dev.py) is an example only.

## Features

- **Sandboxes** — run agents on a Git worktree directly on the host (`NoSandbox`) or in a container (`DockerSandbox`).
- **Agent clients** — Copilot CLI client with live output streaming, session resume, and a dry-run client.
- **Git and GitHub clients** — worktrees, branches, commits, pushes, draft pull requests, and Ticket state, kept in Python rather than left to the agent.
- **Lifecycle hooks** — shell-command hooks run on the host before the agent starts.
- **Stores** — file and in-memory stores for sessions and executions.
- **Test doubles** — fakes for `gh`, `git`, Docker, and the Copilot CLI in `loop.testing`.

## Requirements

- Python 3.12 or newer
- `git`
- GitHub CLI (`gh`) for the GitHub client
- Copilot CLI for the Copilot agent client
- Docker, only when using `DockerSandbox`

## Install

Loop is installed with `pip` from this repository ([ADR 0001](docs/adr/0001-install-loop-with-pip-from-the-repository.md)).

```sh
pip install -e .
```

For development:

```sh
pip install -e ".[dev]"
pytest
```

## Usage

Write a Workflow as a script that imports only from `loop`:

```python
from pathlib import Path

from loop import InMemorySessionStore, NoSandbox, copilot

sandbox = NoSandbox(Path("workspace/my-repo"))
agent = copilot(InMemorySessionStore())

result = sandbox.run(agent, "Fix the failing tests and report what changed.")
```

Run it through your own alias:

```sh
alias dev='python /path/to/harness/workflows/dev.py'
dev --harness-root . --log-level DEBUG
```

See [`workflows/dev.py`](workflows/dev.py) for a complete Workflow and [`workflows/prompts/dev.md`](workflows/prompts/dev.md) for its prompt template.

## Repository layout

| Location | Contents |
|---|---|
| `src/loop/` | The `loop` library |
| `src/loop/testing/` | Test doubles for every process boundary |
| `workflows/` | Example `dev` Workflow and prompt template |
| `tests/` | Test suite, including import-linter architecture checks |
| `docs/` | ADRs, Crosscutting Concepts, and research notes |
| `archive/` | Retired prototypes; parts source only |

## Documentation

- [CONTEXT.md](CONTEXT.md) — domain glossary
- [ARCHITECTURE.md](ARCHITECTURE.md) — building blocks, ADRs, and Crosscutting Concepts
- [System context](docs/building-blocks/system-context.md) — context and container diagrams
