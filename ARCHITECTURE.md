# ralphv2 Overview

A Copilot plugin (`ralph`) of skills for an autonomous development loop and PR review handling, backed by a Python tooling package (`brain-tools`) that exposes the `afk_dev`, `afk_fix_prs`, and `afk_address_prs` CLIs.

## Context

Shared language is defined in [CONTEXT.md](CONTEXT.md).

## Crosscutting Concepts

This section describes crosscutting concepts (practices, patterns, regulations, recurring approaches). They preserve architectural consistency.

| Concept | Trigger condition | Default |
|---------|-------------------|---------|
| **[CLI Vertical Slice Architecture](docs/concepts/str-cli-vertical-slice-architecture.md)** | new CLI command, CLI entry point, vertical slice, use case, feature folder, thin command adapter, exit codes and output rendering, composition root, dependency injection, contract or abstraction for external system, infrastructure adapter, repository or API client, shared domain entity, horizontal handlers/services/commands folder, Python package layout | Implement new CLI behavior as its own use-case slice behind a thin CLI adapter, with external systems reached through contracts wired in the composition root. |
