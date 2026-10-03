# ralphv2 Overview

A Copilot plugin (`ralph`) of skills for an autonomous development loop and PR review handling, backed by a Python tooling package (`brain-tools`) that exposes the `afk_dev`, `afk_fix_prs`, and `afk_address_prs` CLIs.

## Context

Shared language is defined in [CONTEXT.md](CONTEXT.md).

## Architecture Decision Records

An ADR records a point-in-time, localized decision — hard to reverse, surprising without context, and the result of a real trade-off.

| # | Decision | Trigger condition | Summary |
|---|----------|-------------------|---------|
| [0001](docs/adr/0001-install-shipyard-with-pip-from-the-repository.md) | Install Shipyard with pip from the repository | install Shipyard, pip install, brain-tools packaging, distribution channel, PyPI, pipx, zipapp, standalone binary, versioning, delivery to users, running scripts without install, afk_dev entry points | Install `brain-tools` with `pip` from this repository so the repository revision defines the delivered version; no package index, binary, `pipx`, or zipapp distribution. |

## Crosscutting Concepts

This section describes crosscutting concepts (practices, patterns, regulations, recurring approaches). They preserve architectural consistency.

| Concept | Trigger condition | Default |
|---------|-------------------|---------|
| **[CLI Runtime Slice Architecture](docs/concepts/str-cli-runtime-slice-architecture.md)** | new CLI command, CLI entry point, new workflow, slice, slice folder, command registration, command auto-discovery, runtime contracts, adapter contract, composition root in command.py, shared adapter, slice independence, runtime importing slice, Python package layout | Add each workflow as a self-contained slice folder that registers its own command and wires adapters in its command.py; keep contracts and execution policy in runtime, and let the CLI auto-discover slices. |
| **[Ralph Wiggum Loop](docs/concepts/str-ralph-wiggum-loop.md)** | autonomous coding loop, repeated agent sessions, fresh context window, context rot, external task state, shared task list or PRD, binary completion criteria, orchestration strategy, simple loop, planner or reviewer coordination | Preserve fresh sessions, external state, bounded work, and binary completion; use the Simple Loop option unless additional coordination is required. |
| **[Headless AI Agent](docs/concepts/str-headless-ai-agent.md)** | headless agent, non-interactive coding agent, programmatic agent invocation, CLI agent, worktree agent, isolated agent process, agent subprocess, timeout or cancellation, machine-readable completion, Shipyard agent execution | Invoke coding agents as bounded non-interactive processes against an explicit repository/worktree, with durable state external to the session and machine-observable completion. |
| **[AI Agent Team](docs/concepts/str-ai-agent-team.md)** | Crew, AI Agent Team, specialized agents, agent roles, Codey, Testy, Chorey, multi-agent execution, agent coordination, shared agent state, Shipyard crew configuration | Use one general-purpose Headless AI Agent by default; configure a Crew only when distinct specialized responsibilities are needed. |
| **[Platform Adapter](docs/concepts/str-platform-adapter.md)** | platform adapter, GitHub versus Azure DevOps, gh CLI, az CLI, work item normalization, pull request normalization, provider-specific states or fields, platform detection, Git remote platform selection, infrastructure adapter | Use a platform-neutral adapter contract; keep provider CLI invocation and native model translation inside the concrete adapter selected at composition time. |
| **[Autonomous Spec Delivery](docs/concepts/dom-autonomous-spec-delivery.md)** | afk_dev run, AFK dev service, ralph:dev skill, develop spec, spec attempt cap, execution log, actionable issues, hitl label, draft pull request, functional testing tickets, Shipyard CLI Copilot swimlane, dev prompt | Run afk_dev as one fresh, bounded Copilot session per spec with actionable issues, and let the ralph:dev skill deliver that spec end to end. |
| **[Agent Client](docs/concepts/str-agent-client.md)** | agent client, provider client wrapper, CopilotClient wrapper, AI runtime connection, agent session creation, client lifecycle, auto-start, reconnect, connection state, provider SDK isolation, Ralph agent invocation, Crew agent invocation | Use AgentClient as the stable orchestration-facing boundary; keep provider client construction, connection lifecycle, configuration translation, and recovery inside the wrapper. |
| **[Harness Repository Topology](docs/concepts/str-harness-repository-topology.md)** | harness, harness repo, harness root, workspace folder, codebase checkout, single repo harness, multi-repo harness, wrapping harness, worktree location, .worktrees folder, CODEBASE_REPO_PATH, HARNESS_REPO_PATH, code-workspace file, multi-root workspace, parent repository customizations, init-harness, pull-repos | Resolve the harness first, then derive the codebase checkout from the target repository versus the harness origin; keep worktrees beside the checkout and never change code in the checkout itself. |
