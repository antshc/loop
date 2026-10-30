# Usage guide

Start with the [installation and quick start](README.md#installation).
This guide covers the example `implement_spec` Workflow and advanced library usage.
Run repository examples from the repository root.

- [Implement Spec Workflow](#implement-spec-workflow)
- [Other factory Workflows](#other-factory-workflows)
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

## Implement Spec Workflow

The self-contained [`workflows/software_factory/implement_spec/`](workflows/software_factory/implement_spec/) package is an example,
not part of the installed library. It is one declarative definition over the factory's Stage library, run once per Spec by
Loop's Workflow runtime. It uses GitHub Specs and Tickets to drive
agent work, commits, draft pull requests, and Ticket updates.

Install the project (`python -m pip install -e .`); `--otlp-endpoint` needs the `otel` extra (`.[otel]`). Before running it, set `HARNESS_SLUG` and
`harness_target()` in [`__main__.py`](workflows/software_factory/implement_spec/__main__.py) for the repository holding your
Specs and Tickets and receiving the code. The supplied configuration
targets `antshc/loop`; it is not a read-only demo.

```sh
gh auth status
copilot --version
python -m workflows.software_factory.implement_spec
```

Optionally run it through your own alias, from the harness root:

```sh
alias implement_spec='PYTHONPATH=/path/to/harness python -m workflows.software_factory.implement_spec'
implement_spec --log-level DEBUG
```

Exit codes: 0 when the run completed, ended idle (no actionable Ticket, or only Tickets held by dependencies) or held;
1 when a Spec was handed to a person or failed; 130 when interrupted (SIGINT or SIGTERM stops the run between Tickets, and
the next run resumes from the Execution ledger without repeating finished work).

### Implement Spec phases

The definition uses the same phases as the [eShop dev workflow](https://github.com/antshc/eShop/tree/main/tools/workflows/loop/dev):

| Order | Phase | Behavior |
|---|---|---|
| 1 | Prepare repository | Resolve the target/base branch and fetch the repository |
| 2 | Plan and create Tickets | When the Spec has no sub-issues, run the read-only planner, validate its plan, then create sub-issues in dependency order |
| 3 | Deliver Tickets | Run the developer for each actionable Ticket, validate its one commit, rework rejected attempts up to the failure cap, close accepted Tickets and escalate a Ticket that exhausts its attempts to `hitl` |
| 4 | Publish | Push accepted work and create/update the draft PR after each Ticket |
| 5 | Refine changes | Capture the PR diff, run cleanup, and commit/push its changes; discard failed cleanup |
| 6 | Close worktree | Finish implementation before invoking the reviewer |
| 7 | Review pull request | Review the final head from the checkout through the project’s `code-review` skill |
| 8 | Check readiness | Mark ready only when delivery, review axes, held Tickets, and the current head permit it |

These phases run as the normal `implement_spec` workflow, each as a Stage with its own span. Which of the planner, chore and
reviewer run is data on the routed `RepositoryTarget` (`plan`, `chore`, `review`); a Stage whose condition is false is
skipped. A Spec with existing sub-issues, including closed or held ones, is never
planned again.

Loop retains its developer commit contract: the developer makes one tagged commit
per Ticket. Cleanup agents leave edits uncommitted; the harness commits
accepted edits as separate `chore(...)` commits. Planner and reviewer
steps must leave the target checkout unchanged.

Install the project's `code-review` skill in the target
repository before using the reviewer. The reviewer reports all four axes (`bugs`,
`quality`, `smells`, `initiative`) and the reviewed head. Missing/incomplete review,
blocking bug/initiative findings, changed head, or held Tickets keep the PR a draft and fail the Spec result. Cleanup is best effort.
The workflow never merges the PR or closes the Spec.

A Spec or Ticket handed to a person carries the `hitl` label. While it does, a run starts no agent and pushes nothing for it.
Removing `hitl` releases it: the next run writes a release in the Execution ledger and starts a fresh scope with a fresh
rework budget. The budget is counted per work item and release, no longer per UTC day.

Console output is one line per finished span, such as `ticket #12 · implement-ticket attempt 2 · fail · reason`. Commit
validation, restoration, individual gates, agent output, and command details stay
in the Execution ledger, telemetry or the DEBUG file.

```sh
python -m workflows.software_factory.implement_spec --spec 42
python -m workflows.software_factory.implement_spec --dry --fake-speed 0 --no-otel
```

`--spec NUMBER` selects one open Spec. `--no-otel` disables export even when `--dry` selected an endpoint. Existing
`--otlp-endpoint`, fake seams and log-directory options remain available; the Execution ledger lives under
`<log-dir>/ledger`. The terminal table (`--visualize`) is gone: the span log replaces it, and telemetry names are now
Stage ids instead of phase names.

The Stages and routes live in
[`__init__.py`](workflows/software_factory/implement_spec/__init__.py); `explain()` prints which Stage feeds which.

### Debugging with fakes

`--fake <seam>` (repeatable) replaces one external dependency of `implement_spec` with a fake and leaves the
rest real; `--dry` fakes them all and publishes the run to the dashboard (OTLP endpoint
`OTEL_EXPORTER_OTLP_ENDPOINT`, default `http://localhost:4318`).

| Seam | Fakes | Needs |
|------|-------|-------|
| `agent` | the Copilot CLI: a scripted agent that reports usage and tool calls and commits in the worktree | none |
| `git` | the Workflow's and the runtime's Git reads, commits and pushes, and the worktree (a temporary directory) | `agent` |
| `github` | `gh`: one canned Spec with two Tickets; writes change its own state | none |
| `store` | the Execution ledger, in a temporary directory | none |

```sh
python -m workflows.software_factory.implement_spec --dry                                  # everything fake, traces in the dashboard
python -m workflows.software_factory.implement_spec --fake agent --fake github --fake store   # real Git: the fake agent makes real empty commits and `implement_spec` really pushes
python -m workflows.software_factory.implement_spec --dry --fake-speed 0 --no-rework        # instant, no failing first attempt
```

By default the second Ticket fails its first attempt so the rework path shows up in the trace.
In code, `Fakes` in [`dry.py`](workflows/software_factory/implement_spec/dry.py) builds the same fakes, and a test passes
its own `Services` to `main(argv, services=...)`; the library offers
`fake_agent`, `FakeCliRunner` and `DirectoryGit` to run any functional slice in isolation.

### Repositories and targets

The example [`implement_spec`](workflows/software_factory/implement_spec/) reads Specs and Tickets from the harness repository.
By default it implements only Specs targeting the harness itself. Pass a `Targets` service to `main(argv, services=...)`, or
change `harness_target()`, to route Specs by their `repo:target:<owner/repo>` label to
explicitly configured checkouts under the harness's `workspace` folder.
Repositories must already be cloned; unknown targets are handed to a human.

Each `RepositoryTarget` pairs a slug with its checkout and worktrees folder and says whether the planner (`plan`), the chore
cleanup (`chore`) and the reviewer (`review`) run there; the default enables all three. A target with only the developer
runs implementation and leaves the PR a draft. Repository identities are matched
case-insensitively; duplicate configurations fail the run.

The developer Stage handles Ticket iteration, refreshed Initiative history, rework, rollback and Ticket closure: a
declined reply or an invalid commit fails the attempt, and each retry starts a fresh agent session. Per-repository agent
behavior is no longer a Python delegate; adjust the role agents under `workflows/software_factory/agents/roles/`.

After the developer finishes, the runtime requires exactly one new,
correctly tagged commit, a clean worktree, and a reported SHA equal to HEAD.
Existing Initiative commits provide context; a matching subject alone does not
automatically close or skip a Ticket. Git operations and draft PRs use the target
repository; Ticket updates remain on the harness.

### Implement Spec package and named prompts

All `implement_spec`-owned code and tests live in [`workflows/software_factory/implement_spec/`](workflows/software_factory/implement_spec/) and
[`workflows/tests/software_factory/workflows/implement_spec/`](workflows/tests/software_factory/workflows/implement_spec/). The definition places Stages from
[`workflows/software_factory/stages/`](workflows/software_factory/stages/) run by Loop's Workflow runtime ([`src/runtime/`](src/runtime/)),
depends on the public `loop` package (including the Git, GitHub and process helpers in [`src/shared/`](src/shared/)) and on
[`workflows/software_factory/shared/`](workflows/software_factory/shared/) (checks, state, pacing, telemetry), never on a sibling Workflow, and uses no container. The independent
[`plan_implement.py`](workflows/loop/plan_implement.py) is a small definition on the same runtime with its own Stages.

- [`__init__.py`](workflows/software_factory/implement_spec/__init__.py): `implement_spec(settings, targets)`, the definition.
- [`policy.py`](workflows/software_factory/implement_spec/policy.py): values and pure functions only: the `hitl` label, comment source, commit subjects, the Spec work item and when held work is released.
- [`intake.py`](workflows/software_factory/implement_spec/intake.py): selects the open, complete Specs not awaiting a person and writes a release when `hitl` was removed.
- [`__main__.py`](workflows/software_factory/implement_spec/__main__.py): `main(argv, *, services=None)`, flags, logging, the real services and the interrupt signal.
- [`dry.py`](workflows/software_factory/implement_spec/dry.py): the `--fake`/`--dry` seams.
- [`workflows/software_factory/agents/roles/`](workflows/software_factory/agents/roles/): the role agents and their prompt templates (`prompt.md`), read relative to the package, not the current directory.
- [`workflows/tests/software_factory/workflows/implement_spec/`](workflows/tests/software_factory/workflows/implement_spec/): scenarios and harness under the factory test tree; run `pytest workflows/tests/software_factory/workflows/implement_spec`.

To change a prompt, edit the role's `prompt.md`; a missing template raises an error rather
than falling back to a different prompt.

## Other factory Workflows

`pr_babysit` and `address_once` are further definitions over the same Stage library, run by the same Workflow runtime, and use
the same console span log, optional `--otlp-endpoint` export and Execution ledger as `implement_spec`. Both read their
settings from a `--config` file; see [`pr_babysit/settings.example.json`](workflows/software_factory/pr_babysit/settings.example.json)
and [`address_once/settings.example.json`](workflows/software_factory/address_once/settings.example.json).

```sh
python -m workflows.software_factory.pr_babysit --config cfg.json                  # discover your pull requests and babysit each one
python -m workflows.software_factory.pr_babysit --config cfg.json --pr https://github.com/owner/repo/pull/123
python -m workflows.software_factory.address_once --config cfg.json --pr https://github.com/owner/repo/pull/123
```

`pr_babysit` polls a pull request until it closes, addressing feedback, maintaining CI, requesting a Copilot review and
reporting readiness ([PR maintenance](workflows/software_factory/pr_babysit/README.md)); `--include-existing` also enrolls
open pull requests, and `--retry URL` releases a blocked one. `address_once` addresses the trusted review feedback of one pull
request once, assembled only from existing Stages. To write a Workflow of your own, see the
[factory quickstart](workflows/software_factory/README.md) and [Factory Workflow Runtime](docs/concepts/str-factory-workflow-runtime.md).

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

`implement_spec` writes its file to `<log-dir>/implement_spec.log`, and only with `--log-level DEBUG`.

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
records elsewhere. The `implement_spec` Workflow opts in with `python -m workflows.software_factory.implement_spec --otlp-endpoint http://localhost:4318`.
Name a profile from the shared `Role` vocabulary (`replace(DEFAULT, name=Role.REVIEWER)`) and pass `otlp_observer(resource_per_agent=True)` to export each named agent as its own resource, which Aspire colors apart:

| `Role` | Exports |
|--------|---------|
| `ENGINEER` | the request that starts a run (`observer.span("request", role=Role.ENGINEER)`) |
| `CODE` | the Worktree and deterministic steps: checks, validation, Git, GitHub (`observer.span(..., role=Role.CODE)`) |
| `PLANNER`, `DEVELOPER`, `REVIEWER` | the agents of those names (`AgentProfile(name=...)`) |

The Workflow runtime uses `ENGINEER` for each run, `CODE` for scopes and deterministic Stages, and each agent Stage's role (planner, developer, chore, reviewer, address, maintainer) for its attempts; `plan_implement` names its agents `PLANNER` and `DEVELOPER`.
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

See [`workflows/software_factory/implement_spec/`](workflows/software_factory/implement_spec/) for a complete Workflow,
[`workflows/loop/plan_implement.py`](workflows/loop/plan_implement.py) for two runs with
different models on one worktree, and
[`workflows/software_factory/agents/roles/developer/prompt.md`](workflows/software_factory/agents/roles/developer/prompt.md) for
the default `implement_spec` prompt template.



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

See [PR maintenance](workflows/software_factory/pr_babysit/README.md) for continuous PR discovery,
Address/Maintainer profiles, Execution-ledger recovery, and workflow-owned commit/push operations.
