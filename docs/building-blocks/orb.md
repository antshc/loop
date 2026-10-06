# Orb

The `orb` Python library: a single installable package (`pip install` from this repository, [ADR 0001](../adr/0001-install-orb-with-pip-from-the-repository.md)) that a user-owned Workflow script imports to run agent prompts on Git worktrees through Capsules. It ships no command and no built-in Workflows ([ADR 0003](../adr/0003-ship-orb-as-a-workflow-library-with-no-built-in-workflows.md)).

## Dependencies

- **Called by:** Workflow scripts (user-owned; `workflows/dev.py` is the example) through the public `orb` API only.
- **Calls (via subprocess):** `git`, `gh`, `docker`, and the `copilot` CLI. No Python dependency beyond `python-json-logger`.

## Interfaces

Public API re-exported from [`src/orb/__init__.py`](../../src/orb/__init__.py). ABC contracts exist only where Orb calls replaceable parts: `Capsule`, `AgentClient`, `SessionStore`, `ExecutionStore`. `GitClient` and `GitHubClient` are concrete helpers.

## Tweaks/Configuration

No config file. A Workflow declares its settings and hooks as code and injects dependencies into the shipped implementations ([Orb Library Workflow Architecture](../concepts/str-orb-library-workflow-architecture.md)).

## Persisted data

`FileExecutionStore` (attempt counts per key) and `FileSessionStore` (logical agent session keys to provider session names); in-memory variants for tests and dry runs.

## Source-folder structure

Paths relative to `src/orb/`.

```text
__init__.py     public API
contracts/      ABCs: Capsule, AgentClient, SessionStore, ExecutionStore
capsules/       NoCapsule (host), DockerCapsule (container), FakeDocker double
agents/         CopilotClient, DryRunAgentClient, fake agent and Copilot CLI doubles
platforms/      GitClient (git CLI), GitHubClient (gh CLI), fake doubles
stores/         file and in-memory execution/session stores
process.py      CommandExecutor, streaming/cancellable subprocess execution
prompt.py       PromptPreprocessor (prompt placeholders and args)
tags.py         extract_tag, extract_json
parallel.py     parallel_settled
attempts.py     may_attempt (attempt cap policy)
errors.py       OrbError hierarchy
logging_config.py  configure_logging
testing/        public test doubles for every process boundary
```

Dependency rule (enforced by import-linter in `pyproject.toml`): contracts and shared policy import no implementation; only `orb.testing` imports test doubles; `workflows.dev` imports only the public `orb` API.

## Container view

See the solution-level diagram in [system-context.md](system-context.md#solution-container-view); Orb is a single container.

## Component view

```mermaid
---
config:
  c4:
    c4ShapePadding: 20
---
%% diagram-id: orb-component
C4Component
    title Component diagram for orb library

    Container_Ext(workflow, "Workflow script", "Python script", "User-owned script on the public orb API.")

    Container_Boundary(orb, "orb library") {
        Component(capsules, "Capsules", "NoCapsule, DockerCapsule", "Environment for one or more agent runs: workspace, exec, close, and the executor handed to the agent.")
        Component(agents, "Agent clients", "CopilotClient, DryRunAgentClient", "Render the prompt, run the provider CLI through the Capsule's executor, and parse streamed output into a provider-neutral result.")
        Component(platforms, "Git and GitHub clients", "GitClient, GitHubClient", "Worktrees, commit, push, pull requests, and actionable Spec and Ticket queries.")
        ComponentDb(stores, "Stores", "File and in-memory", "Persist session keys and attempt counts.")
        Component(contracts, "Contracts", "ABCs", "Capsule, AgentClient, SessionStore, and ExecutionStore boundaries.")
        Component(policy, "Shared policy", "process, prompt, tags, parallel, attempts, errors", "Command execution, prompt preprocessing, tag extraction, parallel settling, attempt cap, and errors.")
    }

    System_Ext(copilot, "Copilot CLI", "Headless coding agent.")
    System_Ext(docker, "Docker", "Container runtime.")
    System_Ext(github, "GitHub", "Specs, Tickets, and pull requests.")
    System_Ext(git, "Git", "Local repository and worktrees.")

    Rel(workflow, capsules, "Runs agents in")
    Rel(workflow, platforms, "Manages worktrees and tickets with")
    Rel(capsules, agents, "Passes its executor to")
    Rel(agents, stores, "Resolves sessions through")
    Rel(capsules, policy, "Executes commands through")
    Rel(agents, policy, "Renders prompts through")
    Rel(platforms, policy, "Executes commands through")
    Rel(capsules, contracts, "Implements")
    Rel(agents, contracts, "Implements")
    Rel(stores, contracts, "Implements")
    Rel(agents, copilot, "Invokes", "CLI, JSON events")
    Rel(capsules, docker, "Starts and execs containers in", "docker CLI")
    Rel(platforms, github, "Reads and writes", "gh CLI")
    Rel(platforms, git, "Creates worktrees and pushes via", "git CLI")

    UpdateElementStyle(workflow, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(capsules, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(agents, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(platforms, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(stores, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(contracts, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(policy, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(copilot, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(docker, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(github, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(git, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")

    UpdateRelStyle(workflow, capsules, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(workflow, platforms, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(capsules, agents, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(agents, stores, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(capsules, policy, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(agents, policy, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(platforms, policy, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(capsules, contracts, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(agents, contracts, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(stores, contracts, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(agents, copilot, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(capsules, docker, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(platforms, github, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(platforms, git, $textColor="#c9d1d9", $lineColor="#8b949e")
```

## Concepts

Orb is the only Deployable, so its Concepts are system-wide and indexed in [ARCHITECTURE.md](../../ARCHITECTURE.md#crosscutting-concepts).

## Architecture Decision Records

Likewise indexed in [ARCHITECTURE.md](../../ARCHITECTURE.md#architecture-decision-records).

## Key features

- **Capsules:** host (`NoCapsule`) or container (`DockerCapsule`) environment; the agent is passed per run ([ADR 0008](../adr/0008-pass-the-agent-to-each-capsule-run-instead-of-binding-it-to-the-capsule.md)).
- **Agent clients:** Copilot CLI with live output streaming and a dry-run client ([ADR 0007](../adr/0007-stream-agent-output-live-and-parse-it-in-the-provider-adapter.md)).
- **Git and GitHub helpers:** worktrees with `worktree-ready` hooks ([ADR 0004](../adr/0004-run-only-pre-agent-shell-command-hooks-on-the-host.md)); commit, push, and pull requests stay in Python ([ADR 0006](../adr/0006-keep-commit-push-pull-request-and-ticket-state-changes-in-python.md)).
- **Test doubles:** `orb.testing` fakes every process boundary.
