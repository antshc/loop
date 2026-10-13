<p align="left">
  <img src="loop_icon.png" alt="Loop" width="128" height="128">
</p>

# Loop

Python library for composing autonomous agent Workflows on Git worktrees.

Loop ships no command and no built-in Workflows. You write each Workflow as a plain Python script on the public `loop` API and run it through your own shell alias. The repository's [`workflows/dev.py`](workflows/dev.py) module is an example only.

## Features

- **Agent builder** — `Agent()` composes git, Docker, session, and hook options into agent clients and Git worktrees; agents run on the host with the worktree as working directory.
- **Agent profiles** — Copilot CLI and Codex adapters with per-client model and reasoning effort, session resume, and raw stdout as the result.
- **Branch strategies** — worktrees on a named branch, a merged temporary branch, or the repository head; the example Workflows add a GitHub client for draft pull requests and Ticket state, where Python owns push, pull requests, and Ticket state and the agent commits each task.
- **Loop hooks** — shell commands run on the host at `worktree-ready`, `worktree-removing`, and `run-finished`.
- **Dry run** — `AgentOptions(dry_run=True)` exercises the builder with logging-only adapters.

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

### Different agent workflows per repository

The example [`dev`](workflows/dev.py) reads Specs and Tickets from `HARNESS`.
By default it implements only Specs targeting the harness itself. Pass
`repositories=` to route Specs by their `repo:target:<owner/repo>` label to
explicitly configured checkouts under the harness's `workspace` folder.
Repositories must already be cloned; unknown targets are handed to a human.

Each `RepositoryWorkflow` pairs a `Repository` with a function accepting
`(TicketContext, AgentWorkspace)` and returning a `TicketResult`. The function
owns agent profiles, step ordering, prompt rendering, and response decoding.
For example, one repository can implement only while another implements and reviews:

```python
from __future__ import annotations

from functools import partial
from pathlib import Path

from loop import AgentProfile, AgentRequest, AgentWorkspace, copilot
from workflows.dev import PROMPT, DevResponse, RepositoryWorkflow, implement_ticket, main
from workflows.platforms.work_tracking import Repository, RepositoryId
from workflows.tickets import TicketContext, TicketResult

workspace = Path.cwd() / "workspace"
implement = partial(implement_ticket, template=PROMPT.read_text())
reviewer = AgentProfile(copilot)  # Set a model and reasoning effort here if desired.


def implement_and_review(context: TicketContext, worktree: AgentWorkspace) -> TicketResult:
    """Implements the Ticket, then reviews and optionally amends its one commit."""
    result = implement(context, worktree)
    if result.status == "failed":
        return result
    prompt = f"""Review commit {result.commit} against this Ticket:
{context.ticket.title}
{context.ticket.body}

Work only in this worktree. Verify the implementation; fix defects by amending
the existing commit, not by adding another. Keep the commit subject prefix
{context.identifier.to_subject()!r}. Leave the worktree clean.
Do not push, create PRs, or update Tickets.

Return a JSON object with identifier "{context.identifier}" and status
"completed", with result containing commit (the final full HEAD SHA), summary,
and verification. If review cannot complete, return status "failed" with
result containing reason.
"""
    output = worktree.agent(reviewer).run(AgentRequest(prompt)).output
    return DevResponse(context.identifier).decode(output)


if __name__ == "__main__":
    raise SystemExit(main(repositories=(
        RepositoryWorkflow(
            Repository(workspace / "api", workspace / "api.worktrees", RepositoryId("acme", "api")),
            implement,
        ),
        RepositoryWorkflow(
            Repository(workspace / "web", workspace / "web.worktrees", RepositoryId("acme", "web")),
            implement_and_review,
        ),
    )))
```

Run this user-owned script from the harness root. Configure `HARNESS` in
[`dev`](workflows/dev.py) for the repository holding your Specs and Tickets.
The supplied list replaces the default target; include the harness explicitly
if it should also receive code changes. Repository identities are matched
case-insensitively; duplicate configurations fail the run.

The reusable [`deliver_tickets`](workflows/tickets.py) function handles Ticket
iteration, refreshed Initiative history, retries, rollback, failure counts, and
Ticket closure. A delegate can return a failed `TicketResult` or raise
`TicketWorkflowError` for an expected, retryable failure; agent process failures
are also retried. Each retry invokes the whole delegate.

After the delegate finishes, the ticket workflow requires exactly one new,
correctly tagged commit, a clean worktree, and a reported SHA equal to HEAD.
Existing Initiative commits provide context; a matching subject alone does not
automatically close or skip a Ticket. Git operations and draft PRs use the target
repository; Ticket updates and failure-record identities remain on the harness.
Without session-enabled builders, agent steps start fresh sessions.

### Logging

The library logs under the `loop` logger and is silent until you call `configure_logging`:

```python
from pathlib import Path
from loop import configure_logging

configure_logging()                                        # INFO: console only
configure_logging("DEBUG")                                 # DEBUG: console + JSON lines in ./loop.log
configure_logging("DEBUG", log_file=Path("logs/run.log"))  # custom file
configure_logging("DEBUG", log_file=None)                  # DEBUG without a file
```

It configures only the `loop` logger; pass `logger=""` to configure the root logger for your whole application.

`dev` writes its file to `<log-dir>/dev.log`, and only with `--log-level DEBUG`.

### Examples

Every example uses `AgentOptions(dry_run=True)`, which only logs; drop it to run real git and agents.

One-shot run: one git lifecycle per `run()`.

```python
from pathlib import Path

from loop import Agent, AgentOptions, AgentRequest, GitOptions, MergeToHeadStrategy

dry = AgentOptions(dry_run=True)
root, repo = Path("workspace/repo1.worktrees"), Path("workspace/repo1")
Agent(dry).with_git(GitOptions(root, repo, MergeToHeadStrategy())).create().run(
    AgentRequest("Implement ticket #123 in repo1")
)
```

Role profiles; which CLI and model fill a role is a Workflow choice.

```python
from loop import AgentProfile, CodexCli, copilot

PLANNER = AgentProfile(copilot, "claude-opus-4.5", "high")
DEVELOPER = AgentProfile(CodexCli(hook_trust_bypass=True), "gpt-5-codex", "high")
REVIEWER = AgentProfile(copilot, "claude-sonnet-4.5")
```

Shared worktree: one git lifecycle, several CLIs, one named session kept per CLI.

```python
from loop import (
    BranchStrategy,
    SessionEndAgentCliHook,
    SessionName,
    SessionStartAgentCliHook,
    WorktreeReadyLoopHook,
)

loop_hooks = (
    WorktreeReadyLoopHook('cp "$LOOP_REPOSITORY/.env" .env'),
    WorktreeReadyLoopHook("npm ci", timeout_sec=600),
)
shared = Agent(dry).with_git(
    GitOptions(root, repo, BranchStrategy("loop/ticket-123", "main"), loop_hooks=loop_hooks)
).with_session()
shared.with_agent_cli_hooks(
    SessionStartAgentCliHook("./scripts/session-start.sh"),
    SessionEndAgentCliHook("notify-send", timeout_sec=3),
)
with shared.open(session=SessionName("ticket-123")) as wt:
    plan = wt.agent(PLANNER).run(AgentRequest("Plan ticket #123 in repo1"))
    developer = wt.agent(DEVELOPER)
    developer.run(AgentRequest(f"Implement this plan:\n{plan.output}"))
    developer.run(AgentRequest("Fix failing tests"))  # resumes the Codex session
    # A fresh session gives an unbiased review.
    wt.agent(REVIEWER, session=SessionName.new()).run(AgentRequest("Review the diff against the plan"))
```

Without `with_session()`, each run starts under a new `loop-<hex>` name.

```python
fresh = Agent(dry).with_git(GitOptions(root, repo, BranchStrategy("loop/ticket-123", "main"))).create()
fresh.run(AgentRequest("Plan ticket #123 in repo1"))
fresh.run(AgentRequest("Implement ticket #123 in repo1"))
```

See [`workflows/dev.py`](workflows/dev.py) for a complete Workflow, [`workflows/plan_implement.py`](workflows/plan_implement.py) for two runs with different models on one worktree, and [`workflows/prompts/dev.md`](workflows/prompts/dev.md) for the `dev` prompt template.

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
