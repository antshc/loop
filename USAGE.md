# Usage guide

Start with the [installation and quick start](../README.md#installation).
This guide covers the example dev Workflow and advanced library usage.
Run repository examples from the repository root.

- [Dev Workflow](#dev-workflow)
- [Optional Workflow Builder](#optional-workflow-builder)
- [Logging](#logging)
- [Telemetry (optional)](#telemetry-optional)
- [Library examples](#library-examples)
- [Reusable agent profiles](#reusable-agent-profiles)

## Reusable agent profiles

Define an agent once in Python and import it into any Workflow. A profile owns
reusable instructions, CLI settings, tool permissions, and execution defaults;
each `AgentRequest` supplies the task.

```python
# agents/developer.py
from pathlib import Path
from loop import AgentProfile, Instructions, Permissions, copilot

ROOT = Path(__file__).resolve().parents[1]

developer = AgentProfile(
    cli=copilot,
    name="developer",
    # model="your-model",  # Omit to use the installed CLI's default.
    reasoning_effort="high",
    instructions=Instructions.from_file(ROOT / "prompts" / "developer.md"),
    cwd=ROOT,
    add_dirs=(ROOT / "docs",),
    permissions=Permissions(tools="allow-all", deny_tools=("shell(git push)",)),
    env={"DOTNET_ENVIRONMENT": "Development", "LOG_LEVEL": "debug"},
    timeout_sec=1800,
    args=(),
)
```

```python
from agents.developer import developer
from loop import Agent, AgentRequest, BranchStrategy, GitOptions

result = Agent().create(developer).run(AgentRequest("Add cart quantity validation, verify, and commit."))

with Agent().with_git(GitOptions(strategy=BranchStrategy("cart-validation"))).open() as worktree:
    result = worktree.agent(developer).run(AgentRequest("Add cart quantity validation, verify, and commit."))
```

Run the included example without executing Git or agent processes:

```sh
python3 -m examples.profile_workflow
```

Instruction sources can also be literal text or a public raw-text URL:

```python
AgentProfile(copilot, instructions="Implement the task and verify the result.")
AgentProfile(copilot, instructions=Instructions.from_url("https://example.com/developer.md"))
```

Defining or importing a profile does not read its instruction source. Client
creation reads it once; each client keeps a snapshot. Files require absolute
paths. HTTP(S) URLs must return raw UTF-8 text, with a 10-second fetch timeout and
a 1 MiB response limit. Empty content, HTML responses, decoding failures, and
read/fetch errors raise `InstructionsError` before any Agent session is registered.
Pin remote sources to immutable revisions when reproducibility matters.

Every turn, including resumed turns, receives the instructions followed by the
task. This is ordinary prompt composition, without provider-native system-message
priority. Template placeholders remain literal unless the Workflow explicitly
calls `Prompt(...).render()`. Existing profiles without instructions send the
original request unchanged. Dry runs do not read file or URL instruction sources.

| Setting | Resolution |
|---|---|
| `cwd` | Profile overrides builder defaults for direct execution and the root of `with_git().create()` execution. An already opened workspace keeps its directory. |
| `add_dirs`, `env`, `timeout_sec` | Configured profile fields replace builder defaults; `None` inherits them. Empty directories or environment mappings explicitly clear those overrides. |
| Explicit `run(..., context=...)` | Replaces the execution context completely, preserving existing behavior. Git/Docker wrappers may transform it before execution. |
| Harness start mode | Applies after target-directory resolution; adds the target directory and prefixes its navigation instruction. |
| Process environment | Inherits the host environment, then applies configured overrides; Loop's native hook wiring takes precedence. |

Profile directories must be absolute. Process timeouts must be finite and
positive; expiration raises `subprocess.TimeoutExpired`. Environment values are
copied when configured and are not included in Loop's execution logs.

`Permissions(tools="allow-all")` maps to Copilot's `--allow-all-tools`, not its
broader `--allow-all` flag. Alternatively, pass a tuple of native approval patterns,
such as `Permissions(tools=("read", "shell(git diff)"))`. These patterns configure
tool approval, not an exclusive list of available tools or an OS sandbox.
`deny_tools` uses native deny patterns, which take precedence over approvals.
Explicit policies remove Copilot's default broad permission flag. Conflicting
raw tool-permission arguments raise `ValueError`; unsupported policies raise
`UnsupportedPermissions` at client creation. Loop's Codex adapter currently rejects
typed tool policies; use its native `args` for provider-specific settings instead.
When `permissions=None`, the existing adapter defaults remain unchanged.

Git strategies, Docker lifecycle, session storage, observation, and hooks remain
builder-owned. Additional CLI-specific settings remain available through `args`.

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

### Dev phases

The default configuration in [`repositories.py`](workflows/dev/repositories.py)
uses the same phases as the [eShop dev workflow](https://github.com/antshc/eShop/tree/main/tools/loop/workflows/dev):

| Order | Phase | Behavior |
|---|---|---|
| 1 | Prepare repository | Resolve the target/base branch and fetch the repository |
| 2 | Plan and create Tickets | When the Spec has no sub-issues, run the read-only planner, validate its plan, then create sub-issues in dependency order |
| 3 | Deliver Tickets | Run the developer for each actionable Ticket, validate its one commit, retry rejected attempts, and close accepted Tickets |
| 4 | Publish | Push accepted work and create/update the draft PR after each Ticket |
| 5 | Verify application | Discover project verification skills, drive the integrated app, save evidence, and commit valid fixes |
| 6 | Refine changes | Capture the PR diff, run cleanup, and commit/push its changes; discard failed cleanup |
| 7 | Close worktree | Finish implementation before invoking the reviewer |
| 8 | Review pull request | Review the final head from the checkout through the project’s `code-review` skill |
| 9 | Check readiness | Mark ready only when delivery, verification, review axes, held Tickets, and the current head permit it |

These phases run as the normal dev workflow. The default configuration supplies all
delegates; a custom `RepositoryWorkflow` can set an individual delegate to `None` to
skip it. A Spec with existing sub-issues, including closed or held ones, is never
planned again. App verification is skipped when no verification skill is installed.

Loop retains its developer commit contract: the developer makes one tagged commit
per Ticket. Verifier and cleanup agents leave edits uncommitted; the harness commits
accepted edits as separate `fix(...)` or `chore(...)` commits. Planner and reviewer
steps must leave the target checkout unchanged.

Install the project's app-verification skill and `code-review` skill in the target
repository before using those agents. The reviewer reports all four axes (`bugs`,
`quality`, `smells`, `initiative`) and the reviewed head. Missing/incomplete review,
blocking bug/initiative findings, changed head, held Tickets, or an unproven app
verdict keep the PR a draft and fail the Spec result. Cleanup is best effort.
The workflow never merges the PR or closes the Spec.

Console output contains only high-level phase transitions and outcomes. Commit
validation, restoration, individual gates, agent output, and command details stay
in execution history/telemetry or the DEBUG file. Add a terminal table independently:

```sh
python -m pip install -e ".[visualization]"
python -m workflows.dev --spec 42 --visualize
python -m workflows.dev --dry --fake-speed 0 --no-otel --visualize
```

`--spec NUMBER` selects one open Spec. `--visualize` enables the optional Rich table.
`--no-otel` disables export even when `--dry` selected an endpoint. Existing
`--otlp-endpoint`, fake seams, retry settings, and log-directory options remain available.

Phase declarations and actor names live in
[`definition.py`](workflows/dev/definition.py), built on the optional public
`WorkflowBuilder`. Execution stays in the dev-owned Python helpers.

### Debugging with fakes

`--fake <seam>` (repeatable) replaces one external dependency of `dev` with a fake and leaves the
rest real; `--dry` fakes them all and publishes the run to the dashboard (OTLP endpoint
`OTEL_EXPORTER_OTLP_ENDPOINT`, default `http://localhost:4318`).

| Seam | Fakes | Needs |
|------|-------|-------|
| `agent` | the Copilot CLI: a scripted agent that reports usage and tool calls and commits in the worktree | none |
| `git` | the Workflow's Git reads and pushes, and the worktree (a temporary directory) | `agent` |
| `github` | `gh`: one canned Spec with two Tickets; writes are logged, not sent | none |
| `store` | the failure log, in a temporary directory | none |

```sh
python -m workflows.dev --dry                                  # everything fake, traces in the dashboard
python -m workflows.dev --fake agent --fake github --fake store   # real Git: the fake agent makes real empty commits and `dev` really pushes
python -m workflows.dev --dry --fake-speed 0 --no-rework        # instant, no failing first attempt
```

By default the second Ticket fails its first attempt so the retry path shows up in the trace.
In code, `Fakes` in [`dry.py`](workflows/dev/dry.py) builds the same fakes, and the library offers
`fake_agent`, `FakeCliRunner` and `DirectoryGit` to run any functional slice in isolation.

### Different agent workflows per repository

The example [`dev`](../workflows/dev/) reads Specs and Tickets from `HARNESS`.
By default it implements only Specs targeting the harness itself. Pass
`repositories=` to route Specs by their `repo:target:<owner/repo>` label to
explicitly configured checkouts under the harness's `workspace` folder.
Repositories must already be cloned; unknown targets are handed to a human.

Each `RepositoryWorkflow` pairs a `Repository` with a developer function accepting
`(TicketContext, AgentWorkspace)` and returning a `TicketResult`. The function
owns agent profiles, step ordering, prompt rendering, and response decoding. Optional
`run_plan`, `run_verify`, `run_chore`, and `run_review` delegates configure the surrounding
phases; the default configuration supplies all four. A custom instance with only the
repository and developer function runs implementation only and leaves the PR a draft.
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

- [`run.py`](../workflows/dev/run.py): high-level logging, phase reporting, retry settings, Loop hooks, and dependency wiring.
- [`definition.py`](workflows/dev/definition.py): named phases, actor ownership, visibility, and optional terminal/OTLP reporting.
- [`plan.py`](workflows/dev/plan.py), [`verify.py`](workflows/dev/verify.py), [`chore.py`](workflows/dev/chore.py), and [`review.py`](workflows/dev/review.py): planning, integrated app verification, cleanup, review, and readiness checks.
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

## Optional Workflow Builder

`WorkflowBuilder` adds phase tracking, high-level console logs, terminal visualization,
and optional OTLP spans to ordinary Python. It does not execute callbacks or schedule
work: your script owns loops, conditions, retries, Git, and agent calls. Existing
`Agent()` workflows need no changes.

Import from the public package:

```python
from loop import Kind, Phase, Phases, Role, Workflow, WorkflowBuilder, WorkflowRun
```

### Run the fake e-commerce example

[`workflows/workflow_reporting.py`](workflows/workflow_reporting.py) is self-contained,
uses fake basket data, and makes no agent, Git, or GitHub calls. Run from the repository root:

```sh
python -m workflows.workflow_reporting
python -m workflows.workflow_reporting --fail-verification  # exits 1; PR stays a draft
python -m pip install -e ".[visualization]"
python -m workflows.workflow_reporting --visualize
```

The example deliberately rejects the first implementation attempt and recovers on
its second attempt. It publishes a fake draft PR before verification, skips cleanup,
and marks readiness only after verification and review complete.

### Define phases; execute with ordinary Python

This smaller runnable example demonstrates the same interface:

```python
from loop import Kind, Role, WorkflowBuilder, configure_logging

configure_logging()  # The application owns logging handlers.
workflow = (
    WorkflowBuilder("eShop development")
    .phase("prepare", "Prepare repository", Kind.CODE, Role.CODE)
    .phase("implement", "Implement ticket", Kind.AGENT, Role.DEVELOPER)
    .phase("verify", "Verify application", Kind.CODE, "verifier")
    .phase("review", "Review pull request", Kind.AGENT, Role.REVIEWER)
    .console()
    .build()
)

basket = []
with workflow.run(spec=42) as run:
    with run.phase("prepare"):
        basket.clear()

    for ticket, product in ((43, "coffee"), (44, "tea")):
        for attempt in range(1, 3):
            with run.phase("implement", ticket=ticket, attempt=attempt) as phase:
                changed = not (ticket == 43 and attempt == 1)
                if phase.gate("product added", changed, "No changes produced"):
                    basket.append(product)
            if phase.status == "completed":
                break
        else:
            run.fail(f"Ticket #{ticket} exhausted its retries")
            break

    if run.outcome.status != "failed":
        with run.phase("verify") as phase:
            verified = basket == ["coffee", "tea"]
            phase.set(verdict="pass" if verified else "fail")
            phase.gate("basket contains products", verified, "Basket verification failed")
        if verified:
            with run.phase("review") as phase:
                phase.set(blocking_findings=0)
            run.set(ready=True)
        else:
            run.skip("review", "Verification failed")
            run.set(ready=False)
            run.fail("Verification failed; PR remains a draft")

assert run.outcome.status == "completed"
assert run.outcome.attributes["ready"] is True
```

`build()` snapshots the declarations. Each `workflow.run()` creates independent state;
reuse the definition for several Specs. Phase names must be unique and declared before
use. Existing helpers may also pass the declared kind and owner positionally, as with
`Phases.phase(name, kind, owner, **attributes)`; conflicting declarations are rejected.
Definition order controls the pending display, not execution order. The current
interface is synchronous; keep phase contexts within their run and close them before
leaving it. Thread scheduling and asynchronous context managers are outside its scope.

### Outcomes and gates

- `run.phase(...)` starts a new occurrence every time, even for the same Ticket.
  Each occurrence has its own ID, parent ID, attempt attributes, and duration.
- Nested phases inherit their parent's attributes; inner changes do not mutate the parent.
- `phase.gate(check, ok, note)` records a claim and returns `ok`. A failed gate makes
  that phase failed. The caller decides whether to stop, retry, or continue.
- `phase.fail(reason)` is sticky: clean exit and `phase.succeed()` cannot erase failure.
- Clean exit completes a running phase. Exceptions mark it failed and propagate unchanged;
  interruption/cancellation marks it cancelled. Console errors summarize exception types.
- `run.skip(name, reason, **attributes)` records a skipped occurrence without running work.
  A declared phase never entered remains pending; it is not automatically called or skipped.
- A failed child does not automatically fail its parent: retries may recover. Call
  `run.fail(reason)` when recovery fails or a business outcome blocks delivery.
- `phase.set(...)` and `run.set(...)` record business facts; they do not infer success.
  Set verification verdict and PR readiness explicitly. Read the root result through
  `run.outcome` and occurrence history through `run.executions` after exit.

### Independent reporting options

| Option | Behavior | Dependency |
|---|---|---|
| No options | Track phases and gates without output | Base install |
| `.console()` | High-level transitions and final results through `loop.workflow` | Base install |
| `.visualize(stream=...)` | Live terminal table; one final table when redirected | `loop[visualization]` |
| `.otel(observer)` | Export nested phase spans through an existing observer | `loop[otel]` |

Combine the options as needed. Importing Loop never imports Rich or OpenTelemetry.
Missing Rich raises an actionable error only when visualization is requested.
`configure_logging()` or the host's logging configuration controls console records;
the builder installs no handlers. Visualization defaults to stderr, honors `NO_COLOR`,
and restores the terminal on failure. With console and visualization together, each
log transition is emitted once; the table is a separate view of the same phases.
Reporting errors are summarized without replacing the workflow's outcome or original exception.

Use `.phase(..., visible=False)` for details such as commit or restore operations.
They stay in execution history and telemetry but do not appear in console or terminal.
Console output excludes prompts, agent output, tool calls, command output, gate details,
and stack traces. Pass short, high-level reasons to `phase.fail()` and `run.fail()`.

Example console transitions (timestamps/logger omitted):

```text
INFO  eShop development · spec #42 started
INFO  Implement ticket · spec #42 · ticket #43 · attempt 1 started
WARN  Implement ticket · spec #42 · ticket #43 · attempt 1 failed · 0.0s · No changes produced
INFO  Implement ticket · spec #42 · ticket #43 · attempt 2 started
INFO  Implement ticket · spec #42 · ticket #43 · attempt 2 completed · 0.0s
INFO  eShop development · spec #42 completed · 0.0s · ready=True
```

### Share telemetry with agent observation

```python
from loop import Agent, WorkflowBuilder, otlp_observer

observer = otlp_observer(endpoint="http://localhost:4318", resource_per_agent=True)
agent_builder = Agent().with_observer(observer)
workflow_builder = WorkflowBuilder("delivery").otel(observer)
```

Declare phases on `workflow_builder`, build it, and open each phase around the existing
agent/worktree calls. `.otel(observer)` does not attach an agent observer automatically;
`with_observer(observer)` remains the independent agent opt-in. Share the same observer
so spans share providers and trace context. An active workflow phase parents an Agent Run,
including within a worktree; the worktree remains the fallback parent without a current span.
Gates and explicit failed outcomes are exported even when the Python body returns normally.

### Adapt an eShop-style dev workflow

Declare phases in a user-owned `definition.py`, build once in `run.py`, and pass the
active `run` to helpers accepting a phase tracker. The
[eShop reference](https://github.com/antshc/eShop/tree/main/tools/loop/workflows/dev)
adds optional planning/ticket creation before delivery, publishes tickets as they land,
verifies the integrated app, performs optional cleanup, closes the implementation worktree,
then reviews the PR and checks readiness. Preserve that ordering in Python. The builder
adds no GitHub writes, commits, Ticket policy, or review logic.

The actual [`dev` Workflow](#dev-phases) uses the builder for the default phase pipeline.
The smaller fake example demonstrates the library independently. Existing workflow-owned
spans also remain supported.

### Track phases without a builder

```python
from loop import Kind, Phases, Role

phases = Phases()
with phases.phase("verify", Kind.CODE, Role.CODE) as phase:
    phase.gate("basket has coffee", "coffee" in ["coffee"])
```

Direct `Phases()` has no registry and emits no output. The builder and phase tracking
are both optional for agent execution.

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
Name a profile from the shared `Role` vocabulary (`replace(DEFAULT, name=Role.REVIEWER)`) and pass `otlp_observer(resource_per_agent=True)` to export each named agent as its own resource, which Aspire colors apart:

| `Role` | Exports |
|--------|---------|
| `ENGINEER` | the request that starts a run (`observer.span("request", role=Role.ENGINEER)`) |
| `CODE` | the Worktree and deterministic steps: checks, validation, Git, GitHub (`observer.span(..., role=Role.CODE)`) |
| `PLANNER`, `DEVELOPER`, `REVIEWER` | the agents of those names (`AgentProfile(name=...)`) |

`dev` uses `ENGINEER` for each Spec, `CODE` for deterministic phases, and separate planner, developer, verifier, chore, and reviewer actors for agent phases; `plan_implement` names its agents `PLANNER` and `DEVELOPER`.
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



## Worktree recovery and PR maintenance

Loop's Git service manages worktree lifecycle only. It never stages, commits, or
pushes changes. The Workflow or agent must explicitly commit before a successful
worktree exit. `GitOptions.commit_message` has been removed.

- Clean success or failure removes the directory and retains named-branch commits.
- Dirty failure preserves the directory and propagates the original error.
- A successful body leaving edits raises `WorktreeError` and preserves the directory.
- Opening the same branch/path validates repository ownership and branch identity,
  then reuses preserved work. Existing local commits are never silently reset.
- An explicit `BranchStrategy(..., base_branch=...)` starts a new local branch from
  that exact ref. Without it, an existing origin branch is preferred over HEAD.
- Successful `MergeToHeadStrategy` runs fast-forward the host and delete the temporary
  branch. Failed/divergent merges retain that branch for Workflow-owned recovery.
- Stale metadata is pruned. Empty orphan directories or lone broken Git links can be
  removed; nonempty unrecognized directories are preserved and reported.
- The Workflow must serialize users of the same named worktree. No library lock is
  implied. Relative roots remain inside cwd; absolute roots are explicit locations.

Worktree observers emit `retained` when a directory remains, instead of claiming it
was removed. OTLP closes the run's span and marks retention independently of disk cleanup.
Loop has no isolated sandbox synchronization backend; patch-based sync recovery is
not applicable to its host worktrees or Docker context adapter.

See [PR maintenance](workflows/pr_babysit/README.md) for continuous PR discovery,
Address/Maintainer profiles, JSON recovery, and workflow-owned commit/push operations.
