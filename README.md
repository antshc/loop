<p align="left">
  <img src="loop_icon.png" alt="Loop" width="128" height="128">
</p>

# Loop

Python library for composing autonomous coding-agent Workflows on Git worktrees.

Use Loop to run Copilot CLI or Codex from Python, manage branches and worktrees,
and coordinate multiple agent steps. You own the prompts, control flow, and
interpretation of results; Loop provides the execution and lifecycle building blocks.
It ships no command and no built-in Workflows.

[Installation](#installation) | [Quick start](#quick-start) | [Examples](#examples) | [Documentation](#documentation) | [Contributing](#contributing)

## Features

- **Composable agents:** choose a CLI, model, and reasoning effort per agent profile.
- **Reusable Python profiles:** import instructions, tool permissions, directories,
  environment, and timeouts into your Workflows. See [profile examples](USAGE.md#reusable-agent-profiles).
- **Git lifecycle management:** use a named-branch worktree, a temporary branch
  merged back into HEAD, or the repository directory directly.
- **Multi-step Workflows:** share a worktree across agents and optionally resume sessions.
- **Lifecycle commands:** run host shell commands at `worktree-ready`,
  `worktree-removing`, and `run-finished` Loop hook points.
- **Dry runs:** log Git and agent operations before enabling real execution.

## Requirements

- Python 3.14 or newer, for both the library and example Workflows.
- Git for repository and worktree operations.
- [Copilot CLI](https://github.com/github/copilot-cli) or
  [Codex CLI](https://github.com/openai/codex), installed and authenticated for live agent runs.
- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated, only for examples
  that interact with GitHub.

## Installation

Install from this repository, not from PyPI. In a shell:

```sh
git clone https://github.com/antshc/loop.git
cd loop
python -m venv .venv
```

Activate the virtual environment:

```sh
# Linux / macOS
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Upgrade the packaging tools first. Old `pip`/`setuptools` cannot read
`pyproject.toml` metadata and silently install an empty `UNKNOWN-0.0.0` package:

```sh
python -m pip install --upgrade pip "setuptools>=68" wheel
```

Then install the library:

```sh
python -m pip install .
```

To install without cloning, run `python -m pip install git+https://github.com/antshc/loop`
after the upgrade. Verify with `python -m pip show loop`: the name must be `loop`, not `UNKNOWN`.
If `UNKNOWN` was installed, run `python -m pip uninstall UNKNOWN` and reinstall.

The repository revision determines the installed version. To update, pull the
desired revision and reinstall. For an editable development installation, see
[Contributing](#contributing).

## Quick start

Save this as `my_workflow.py` and run it from the Git repository you want to work
on. It starts in **dry-run mode**: Git and agent operations are logged, not executed.

```python
from loop import (
    Agent,
    AgentOptions,
    AgentProfile,
    AgentRequest,
    BranchStrategy,
    GitOptions,
    configure_logging,
    copilot,
)

configure_logging()
options = GitOptions(strategy=BranchStrategy("loop/fix-tests"))

with Agent(AgentOptions(dry_run=True)).with_git(options).open() as worktree:
    agent = worktree.agent(AgentProfile(copilot))
    result = agent.run(AgentRequest("Fix the failing tests, verify, and commit. Do not push."))
    print(result.output)
```

```sh
python my_workflow.py
```

To run it for real, replace `Agent(AgentOptions(dry_run=True))` with `Agent()`.
The example uses the CLI's default model and reasoning effort. It creates or
reuses `loop/fix-tests`, starting a new branch from HEAD, and runs the agent in
`.worktrees/loop/fix-tests`. Loop removes clean worktrees on success or failure;
the local branch and its commits remain. Dirty worktrees are preserved, and a
successful body leaving edits raises `WorktreeError`. Loop never commits or pushes
automatically: the agent or Workflow must explicitly commit verified changes.

`result.output` contains the agent's output text, not a validated task outcome.
Your Workflow must interpret it and verify the work.

> **Execution safety:** agents run on the host with your user permissions.
> Worktrees separate code changes; they are not a security sandbox. The default
> Copilot adapter allows all tools. Use trusted repositories and prompts, and
> review changes before publishing them.

## Examples

The examples live in this repository and are not included in the installed
`loop` package.

| Example | Purpose |
|---|---|
| [Library usage](USAGE.md#library-examples) | One-shot runs, profiles, shared worktrees, sessions, and Loop hooks |
| [PR scheduler and babysitter](workflows/software_factory/workflows/pr_babysit/README.md) | Discover user-authored PRs and address reviews/CI with ledger recovery and scoped telemetry |
| [Address one pull request once](USAGE.md#other-factory-workflows) | Address the trusted review feedback of one pull request with `address_once`, assembled from existing Stages |
| [Write a factory Workflow](workflows/software_factory/README.md) | Compose a Workflow definition from the factory's Stages and run it with Loop's Workflow runtime |
| [Plan and implement](workflows/loop/plan_implement.py) | Plan and implement with different models on one worktree |
| [Implement Spec Workflow guide](USAGE.md#implement-spec-workflow) | Deliver GitHub Tickets, configure multiple repositories, and customize prompts |
| [Multi-repository Workflow](USAGE.md#repositories-and-targets) | Route Specs to several repositories, each with its own agent steps |

The `implement_spec` Workflow can create commits, push branches, open draft pull requests,
and update Tickets. Read its configuration guide before running it.

### Multi-repository example

The `implement_spec` Workflow routes Specs by their `repo:target:<owner/repo>` label to
repositories you configure. Each `RepositoryTarget` pairs a checkout with whether the
planner, the chore cleanup and the reviewer run there; here `api` only implements while `web`
implements and then reviews. Repositories must already be cloned under
`workspace/`, and unknown targets are handed to a human.

```python
from pathlib import Path

from workflows.software_factory.events.specs import RepositoryTarget
from workflows.software_factory.workflows.implement_spec.__main__ import build_services, main
from workflows.software_factory.workflows.implement_spec.dry import Fakes
from workflows.software_factory.workflows.implement_spec.settings import Targets

workspace = Path.cwd() / "workspace"
targets = Targets((
    RepositoryTarget("acme/api", workspace / "api", workspace / "api.worktrees", plan=False, chore=False, review=False),
    RepositoryTarget("acme/web", workspace / "web", workspace / "web.worktrees"),
))

if __name__ == "__main__":
    raise SystemExit(main(services=build_services(Fakes([]), None).bind(Targets, targets)))
```

Run it from the harness root. The [usage guide](USAGE.md#repositories-and-targets)
has the delivery rules.

## Repository layout

| Path | Contents |
|---|---|
| `src/` | The installable `loop` library, including the Workflow runtime and the shared Git and process helpers. The only installed code. |
| `tests/` | Library tests, grouped by capability. |
| `workflows/loop/` | The standalone `plan_implement` example on the public `loop` API. |
| [`workflows/software_factory/`](workflows/software_factory/README.md) | Software factory built on `loop`: reusable Stages, Flow fragments, role agents, and Workflow definitions such as `implement_spec` and `pr_babysit`. Imports only the public `loop` package and runs from the repository clone. |
| `workflows/tests/` | Workflow tests for `loop/` and `software_factory/`. |

## Documentation

- [Usage guide](USAGE.md): advanced examples, logging, and `implement_spec` Workflow configuration.
- [Software factory](workflows/software_factory/README.md): write and run factory Workflows from reusable Stages.
- [Domain glossary](GLOSSARY.md): project terminology.
- [Architecture](ARCHITECTURE.md): codebase structure, design decisions, and concepts.
- [System context](docs/building-blocks/system-context.md): context and container diagrams.
- [Installation decision](docs/adr/install-loop-with-pip-from-the-repository.md): distribution and versioning.

## Contributing

From the repository root, with your virtual environment active:

```sh
python -m pip install -e ".[dev]" --config-settings editable_mode=strict
python -m pytest
```

The strict editable mode keeps `import loop` resolving to the repository-root `src/` folder.
The test suite includes library tests, example Workflow tests, and import-linter
architecture checks. See the [test strategy](docs/concepts/ops-test-strategy.md)
for testing boundaries.

For compatibility changes, verify installation, tests, and Workflow entry points on the minimum supported interpreter (Python 3.14); see [Python compatibility](docs/concepts/ops-test-strategy.md#python-compatibility).

For bug reports and feature requests, [open an issue](https://github.com/antshc/loop/issues).
Include reproduction steps and relevant Python and agent CLI versions for bugs;
remove credentials and sensitive data from logs before sharing them.

