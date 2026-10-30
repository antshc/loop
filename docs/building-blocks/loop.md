# Loop

The `loop` Python library: a single installable package (`pip install` from this repository, [Install Loop with pip](../adr/install-loop-with-pip-from-the-repository.md)) that a user-owned Workflow script imports to run agent prompts on Git worktrees. It ships no command and no built-in Workflows ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).

## Dependencies

- **Called by:** User-owned Workflows (`software_factory/workflows/implement_spec/` is the self-contained example) through the public `loop` API only.
- **Calls (via subprocess):** `git`, the `copilot` and `codex` CLIs, and optionally `docker`. No Python dependency beyond `python-json-logger`; the optional `otel` extra adds the OpenTelemetry SDK and OTLP/HTTP exporter.

## Interfaces

Public API re-exported from [`src/loop/__init__.py`](../../src/loop/__init__.py): `Agent()` returns an `AgentBuilder` ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)), configured with `with_git(GitOptions)`, `with_docker`, `with_session`, and `with_agent_cli_hooks`; `create()` yields an `AgentClient` and `open()` a `Worktree` whose `agent(profile)` yields clients. An `AgentProfile` picks the CLI (`copilot`, `codex`), model, and reasoning effort; a run takes an `AgentRequest` and returns an `AgentResult`. Replaceable seams are `CliRunner`, `GitService`, `DockerService`, `SessionStore`, and, when a Workflow opts in with `with_observer`, `RunObserver` and `SessionLogReader`. The GitHub client and the Spec/Ticket tracker are not part of the public API: the Workflow-side GitHub client lives in `software_factory/src/shared/github/` and implement_spec's definition and policy in `software_factory/workflows/implement_spec/`.

## Tweaks/Configuration

No config file. A Workflow declares its settings and hooks as code and injects dependencies into the shipped implementations ([Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md)).

## Persisted data

A `SessionStore` maps logical agent session keys to provider session identifiers when a Workflow calls `with_session()`; an in-memory store is provided. Nothing else is persisted by the library; Workflows own their own state.

## Source-folder structure

Paths relative to `loop/src/`.

```text
__init__.py     public API
factory.py      Agent() composition root
builder.py      AgentBuilder, Worktree
dryrun/         logging-only adapters for the demo, plus FakeCliRunner and DirectoryGit for isolated end-to-end runs
agents/         AgentClient, AgentProfile, copilot and codex profiles, wrappers over a client
clis/           CliRunner, the Copilot and Codex CLI adapters, the observing runner and the Copilot session-log reader
docker/         DockerRuntime and DockerService
git/            GitOptions, BranchStrategy, MergeToHeadStrategy, HeadStrategy, GitService, git CLI, observing Git service
run/            AgentRequest, AgentResult, RunContext
hooks/          Loop hooks (worktree-ready, worktree-removing, run-finished), Agent CLI hooks
sessions/       SessionStore and session values
observe/        RunObserver, AgentRunRecord, Usage, ToolCall, WorktreeEvent; otel.py is the optional OpenTelemetry observer (extra `loop[otel]`)
```

Dependency rule (enforced by import-linter in `pyproject.toml`): a strict layering `factory` > `builder | dryrun` > `agents` > `clis | docker | git` > `run` > `hooks` > `sessions` > `observe`; only `observe/otel.py` imports `opentelemetry` (checked by a unit test); Workflows import only the public `loop` API, implement_spec imports no sibling helpers, and Ticket orchestration imports no agent delegate.

## Container view

See the solution-level diagram in [system-context.md](system-context.md#solution-container-view); Loop is a single container.

## Component view

```mermaid
---
config:
  c4:
    c4ShapePadding: 20
---
%% diagram-id: loop-component
C4Component
    title Component diagram for loop library

    Container_Ext(workflow, "Workflow script", "Python script", "User-owned script on the public loop API.")

    Container_Boundary(loop, "loop library") {
        Component(builder, "Agent builder", "Agent(), AgentBuilder, Worktree", "Composes git, Docker, session, and hook options into clients and worktrees.")
        Component(agents, "Agent clients", "AgentClient, AgentProfile", "Send one AgentRequest per run for one profile and return the raw output.")
        Component(clis, "CLI runners", "CliRunner, Copilot and Codex adapters", "Build and run the agent CLI command and report its output, session handle, and exit code.")
        Component(git, "Git", "GitOptions, BranchStrategy, GitService", "Creates and removes worktrees on a branch strategy.")
        Component(docker, "Docker", "DockerService", "Optional runtime for running the agent CLI in a container.")
        Component(hooks, "Hooks", "LoopHook, AgentCliHook", "Run Loop hooks on the host at three points; install observe-only Agent CLI hooks.")
        ComponentDb(sessions, "Sessions", "SessionStore", "Maps logical session keys to provider sessions.")
        Component(observe, "Observation", "RunObserver, otlp_observer", "Optional: reports Agent Runs and Worktrees to an observer; exports OpenTelemetry spans and metrics.")
    }

    System_Ext(cli, "Agent CLI", "Copilot or Codex, headless.")
    System_Ext(gitext, "Git", "Local repository and worktrees.")

    Rel(workflow, builder, "Builds agents and worktrees with")
    Rel(builder, agents, "Creates")
    Rel(builder, git, "Opens worktrees through")
    Rel(agents, clis, "Runs prompts through")
    Rel(agents, docker, "Optionally wraps runs in")
    Rel(git, hooks, "Runs Loop hooks through")
    Rel(clis, sessions, "Resumes sessions through")
    Rel(clis, observe, "Optionally reports runs to")
    Rel(git, observe, "Optionally reports worktrees to")
    Rel(clis, cli, "Invokes", "CLI")
    Rel(git, gitext, "Runs worktree commands via", "git CLI")

    UpdateElementStyle(workflow, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(builder, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(agents, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(clis, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(git, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(docker, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(hooks, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(sessions, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(observe, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(cli, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(gitext, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")

    UpdateRelStyle(workflow, builder, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(builder, agents, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(builder, git, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(agents, clis, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(agents, docker, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(git, hooks, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(clis, sessions, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(clis, cli, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(git, gitext, $textColor="#c9d1d9", $lineColor="#8b949e")
```

## Concepts

Loop is the only Deployable, so its Concepts are system-wide and indexed in [ARCHITECTURE.md](../../ARCHITECTURE.md#crosscutting-concepts).

## Architecture Decision Records

Likewise indexed in [ARCHITECTURE.md](../../ARCHITECTURE.md#architecture-decision-records).

## Key features

- **Agent builder:** `Agent()` composes git, Docker, session, and hook options into agent clients and worktrees; agents run on the host with the worktree as working directory ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- **Agent profiles:** the CLI (Copilot, Codex), model, and reasoning effort are chosen per client; the library returns the CLI's raw stdout and the Workflow parses it.
- **Git worktrees and hooks:** `BranchStrategy`, `MergeToHeadStrategy`, and `HeadStrategy` choose the worktree branch; Loop hooks run at `worktree-ready`, `worktree-removing`, and `run-finished`. Push, pull requests, and Ticket state stay in Workflow Python, while the agent commits each task ([Run one fresh agent per Ticket](../adr/run-one-fresh-agent-per-ticket-from-python-and-let-the-agent-commit-it.md)).
- **Dry run:** `AgentOptions(dry_run=True)` exercises the builder with logging-only adapters; `fake_agent(FakeCliRunner(...))` runs a scripted agent in a temporary directory and, passed as the observer's reader, reports its usage and tools like a real CLI.
- **Observation (optional):** `with_observer(observer)` reports each Agent Run turn (usage and tool calls from the CLI's session log) and each Worktree to a `RunObserver`; `otlp_observer(...)` (extra `loop[otel]`) exports OpenTelemetry spans and metrics over OTLP to Aspire or any backend. Nothing changes, and `opentelemetry` is never imported, without it ([Observe Agent Runs through an optional observer seam](../adr/observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md)).
- **Logging:** the library logs under the `loop` logger and stays silent until the host calls `configure_logging()`. INFO goes to the console only; DEBUG adds detail and also writes JSON lines to a configurable file (`log_file=None` disables it).
