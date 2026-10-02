# ralphv2 Overview

A Copilot plugin (`ralph`) of skills for an autonomous development loop and PR review handling, backed by a Python tooling package (`brain-tools`) that exposes the `afk_dev`, `afk_fix_prs`, and `afk_address_prs` CLIs.

## Context

Shared language is defined in [CONTEXT.md](CONTEXT.md).

## Crosscutting Concepts

This section describes crosscutting concepts (practices, patterns, regulations, recurring approaches). They preserve architectural consistency.

| Concept | Trigger condition | Default |
|---------|-------------------|---------|
| **[CLI Vertical Slice Architecture](docs/concepts/str-cli-vertical-slice-architecture.md)** | new CLI command, CLI entry point, vertical slice, use case, feature folder, thin command adapter, exit codes and output rendering, composition root, dependency injection, contract or abstraction for external system, infrastructure adapter, repository or API client, shared domain entity, horizontal handlers/services/commands folder, Python package layout | Implement new CLI behavior as its own use-case slice behind a thin CLI adapter, with external systems reached through contracts wired in the composition root. |
| **[Ralph Wiggum Loop](docs/concepts/str-ralph-wiggum-loop.md)** | autonomous coding loop, repeated agent sessions, fresh context window, context rot, external task state, shared task list or PRD, binary completion criteria, orchestration strategy, simple loop, planner or reviewer coordination | Preserve fresh sessions, external state, bounded work, and binary completion; use the Simple Loop option unless additional coordination is required. |
| **[Headless AI Agent](docs/concepts/str-headless-ai-agent.md)** | headless agent, non-interactive coding agent, programmatic agent invocation, CLI agent, worktree agent, isolated agent process, agent subprocess, timeout or cancellation, machine-readable completion, Shipyard agent execution | Invoke coding agents as bounded non-interactive processes against an explicit repository/worktree, with durable state external to the session and machine-observable completion. |
| **[AI Agent Team](docs/concepts/str-ai-agent-team.md)** | Crew, AI Agent Team, specialized agents, agent roles, Codey, Testy, Chorey, multi-agent execution, agent coordination, shared agent state, Shipyard crew configuration | Use one general-purpose Headless AI Agent by default; configure a Crew only when distinct specialized responsibilities are needed. |
| **[Platform Adapter](docs/concepts/str-platform-adapter.md)** | platform adapter, GitHub versus Azure DevOps, gh CLI, az CLI, work item normalization, pull request normalization, provider-specific states or fields, platform detection, Git remote platform selection, infrastructure adapter | Use a platform-neutral adapter contract; keep provider CLI invocation and native model translation inside the concrete adapter selected at composition time. |
