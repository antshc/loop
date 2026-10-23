# Usage guide

Start with the [installation and quick start](../README.md#installation).
This guide covers the example dev Workflow and advanced library usage.
Run repository examples from the repository root.

- [Dev Workflow](#dev-workflow)
- [Scenario-style flow visualization (optional)](#scenario-style-flow-visualization-optional)
- [Logging](#logging)
- [Telemetry (optional)](#telemetry-optional)
- [Library examples](#library-examples)

## Dev Workflow

The self-contained [`workflows/dev/`](../workflows/dev/) package is an example,
not part of the installed library. It uses GitHub Specs and Tickets to drive
agent work, commits, draft pull requests, and Ticket updates.

Before running it, configure `HARNESS` and target repositories in
[`repositories.py`](../workflows/dev/repositories.py). The supplied configuration
targets `antshc/loop`; it is not a read-only demo.

```sh
gh auth status
copilot --version
python -m workflows.dev
```

Optionally run it through your own alias, from the harness root:

```sh
alias dev='PYTHONPATH=/path/to/harness python -m workflows.dev'
dev --log-level DEBUG
```

### Different agent workflows per repository

The example [`dev`](../workflows/dev/) reads Specs and Tickets from `HARNESS`.
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
from workflows.dev import (
    DevResponse, Repository, RepositoryId, RepositoryWorkflow,
    TicketContext, TicketResult, implement_ticket, load_prompt, main,
)

workspace = Path.cwd() / "workspace"
implement = partial(implement_ticket, template=load_prompt("implement"))
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
[`repositories.py`](../workflows/dev/repositories.py) for the repository holding your Specs and Tickets.
The supplied list replaces the default target; include the harness explicitly
if it should also receive code changes. Repository identities are matched
case-insensitively; duplicate configurations fail the run.

The dev-owned [`deliver_tickets`](../workflows/dev/attempt.py) function handles Ticket
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

### Dev package and named prompts

All dev-owned code, prompts, tests, and fakes live in [`workflows/dev/`](../workflows/dev/).
It depends on the public `loop` package, not sibling Workflow helpers.
The independent [`plan_implement.py`](../workflows/plan_implement.py) is a self-contained,
function-based script: prompt text, response parsing, Git reads, and orchestration
live together, with no sibling Workflow imports.
There is no shared helper layer under `workflows/`; each Workflow owns the helpers it uses.

- [`run.py`](../workflows/dev/run.py): logging, retry settings, Loop hooks, and dependency wiring.
- [`repositories.py`](../workflows/dev/repositories.py): `HARNESS` and `configured_repositories`; edit its returned tuple to configure several repositories for `python -m workflows.dev`, or pass `repositories=` to `main`.
- [`backlog.py`](../workflows/dev/backlog.py) and [`attempt.py`](../workflows/dev/attempt.py): Spec routing/publication and Ticket delivery.
- [`agent.py`](../workflows/dev/agent.py), [`reply.py`](../workflows/dev/reply.py), and [`journal.py`](../workflows/dev/journal.py): prompt handling, response interpretation, and failure history.
- [`records.py`](../workflows/dev/records.py): work items, repository identities, and outcomes.
- [`checkout.py`](../workflows/dev/checkout.py), [`platforms.py`](../workflows/dev/platforms.py), and [`commands.py`](../workflows/dev/commands.py): Git, GitHub, and subprocess access.
- [`prompts/`](../workflows/dev/prompts/): named Markdown templates; `load_prompt("implement")` reads `implement.md` relative to the package, not the current directory.
- [`tests/`](../workflows/dev/tests/): dev tests and local fakes; run `pytest workflows/dev/tests`.

Add templates to the prompt folder and let each repository's delegate select,
render, or combine them. For example, after adding `review.md`, use
`render_prompt(load_prompt("review"), {"COMMIT": result.commit})` with that template's
arguments. Delegates own step ordering and agent profiles; there is no prompt
pipeline or configuration-file format. A missing template raises an error rather
than falling back to a different prompt.

## Scenario-style flow visualization (optional)

[`workflows/dev_bdd/`](workflows/dev_bdd/) is a dummy scenario-driven Workflow (fake data, no GitHub, Git
or agent calls). Flows are plain Python scenarios made of named steps, and each run prints a live,
indented tree of what is executing. It is code-first: no Gherkin and no Given/When/Then. Nothing in
`src/loop` depends on it.

```sh
python -m workflows.dev_bdd
```

```
> Development workflow
  > Process specification #100
    > Deliver ticket #101
      > Execute agent
      < Execute agent [SUCCESS] 41.20s
```

Options:

| Option | Meaning |
|---|---|
| `--quiet` | Disable the live progress on stderr |
| `--no-allure` | Disable Allure results |
| `--allure-results DIR` | Allure results directory (default `<log-dir>/allure-results`); an error if `allure-commons` is missing |

Progress is colored on a terminal and plain when piped or when `NO_COLOR` is set.

Allure results are optional and on by default. Install the extra to produce them; without it the
Workflow logs a warning and runs normally.

```sh
python -m pip install -e ".[reports]"
python -m workflows.dev_bdd
allure serve .loop/allure-results
```

The Allure adapter is single-threaded and has not yet been verified against a viewer.

Layers (`run -> scenarios -> steps -> services`) and their rules are in
[`workflows/dev_bdd/AGENTS.md`](workflows/dev_bdd/AGENTS.md). To apply the same structure to another
project, use the reusable prompt [`docs/prompts/restructure-to-scenarios.md`](docs/prompts/restructure-to-scenarios.md).

## Logging

The library logs under the `loop` logger and installs no output handlers.
Use your application's logging configuration or call `configure_logging`:

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

## Telemetry (optional)

Install the extra, start an OTLP backend such as the standalone
[Aspire dashboard](https://aspire.dev/dashboard/standalone/) (UI `:18888`, OTLP/HTTP `:4318`;
see [`docker-compose.yml`](docs/prototypes/dashboard-aspire/docker-compose.yml)), and opt in on the builder:

```sh
python -m pip install -e ".[otel]"
```

```python
from loop import Agent, AgentRequest, DEFAULT, otlp_observer

observer = otlp_observer(endpoint="http://localhost:4318", service_name="my-workflow")
with Agent().with_git(options).with_observer(observer).open() as worktree:
    with observer.span("#1 add basket", {"loop.ticket.number": 1}):    # your own span; runs nest under it
        worktree.agent(DEFAULT).run(AgentRequest("..."))
```

Each Agent Run becomes an `invoke_agent` span with `execute_tool` children, token, AIU and
premium-request usage (read from the Copilot CLI's session log), and the Worktree a
`loop.worktree` span; metrics go to the same backend. Prompts, outputs and tool arguments are
exported only with `otlp_observer(capture_content=True)`. Without `with_observer` nothing changes.
A span appears in the dashboard when it ends, and Codex Agent Runs carry no usage. Pass any
object with `agent_run(record)` and `worktree(event)` methods to `with_observer` to send the
records elsewhere. The dev Workflow opts in with `python -m workflows.dev --otlp-endpoint http://localhost:4318`.
See [Run Observation](docs/concepts/str-run-observation.md).

## Library examples

The snippets in this section build on one another. They use
`AgentOptions(dry_run=True)` to log Git and agent operations without executing
them. Remove that option only when ready to run real Git operations and agents.

One-shot run: one git lifecycle per `run()`.

```python
from pathlib import Path

from loop import Agent, AgentOptions, AgentRequest, GitOptions, MergeToHeadStrategy, configure_logging

configure_logging()
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

By default the CLI starts inside the worktree. To start it in the harness checkout
instead (so it loads the harness context), set `start_mode` and `harness_dir`; Loop
grants the worktree with `--add-dir` and prefixes each prompt with a `cd` instruction:

```python
from loop import Agent, AgentOptions, AgentStartMode

Agent(AgentOptions(start_mode=AgentStartMode.HARNESS, harness_dir=Path("/path/to/harness")))
```

See [`workflows/dev/`](../workflows/dev/) for a complete Workflow,
[`workflows/plan_implement.py`](../workflows/plan_implement.py) for two runs with
different models on one worktree, and
[`workflows/dev/prompts/implement.md`](../workflows/dev/prompts/implement.md) for
the default `dev` prompt template.
