# System Context

## System context

```mermaid
---
config:
  c4:
    c4ShapePadding: 20
---
%% diagram-id: loop-system-context
C4Context
    title System Context diagram for Loop

    Person(author, "Workflow author", "Writes and runs Workflow scripts through their own shell alias.")

    System(loop, "Loop", "Python library for composing autonomous agent Workflows on Git worktrees.")

    System_Ext(github, "GitHub", "Hosts the repository, Specs, Tickets, review threads, and pull requests.")
    System_Ext(copilot, "Copilot CLI", "Headless coding agent that does the Crew's work.")
    System_Ext(git, "Git", "Local repository, worktrees, commits, and pushes.")

    Rel(author, loop, "Runs Workflows built on", "Python")
    Rel(loop, github, "Reads Tickets and writes pull requests via", "gh CLI")
    Rel(loop, copilot, "Runs prompts through", "CLI, JSON events")
    Rel(loop, git, "Creates worktrees, commits, and pushes via", "git CLI")

    UpdateElementStyle(author, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#4a5a8a")
    UpdateElementStyle(loop, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(github, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(copilot, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(git, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")

    UpdateRelStyle(author, loop, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(loop, github, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(loop, copilot, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(loop, git, $textColor="#c9d1d9", $lineColor="#8b949e")
```

## Solution container view

```mermaid
---
config:
  c4:
    c4ShapePadding: 20
---
%% diagram-id: loop-solution-container
C4Container
    title Solution container diagram for Loop

    Person(author, "Workflow author", "Writes and runs Workflow scripts through their own shell alias.")

    Container_Ext(workflow, "Workflow script", "Python script", "User-owned runnable script on the public loop API; the repository's dev Workflow is only an example.")

    System_Boundary(system, "Loop") {
        Container(loop, "loop library", "Python package", "Agent runner, agent clients, git services, stores, and shared policy for composing Workflows.")
    }

    System_Ext(github, "GitHub", "Hosts the repository, Specs, Tickets, and pull requests.")
    System_Ext(copilot, "Copilot CLI", "Headless coding agent.")
    System_Ext(git, "Git", "Local repository and worktrees.")

    Rel(author, workflow, "Runs", "Shell alias")
    Rel(workflow, loop, "Composes Agent Runs with", "Python import")
    Rel(loop, github, "Reads Tickets and writes pull requests via", "gh CLI")
    Rel(loop, copilot, "Runs prompts through", "CLI, JSON events")
    Rel(loop, git, "Creates worktrees, commits, and pushes via", "git CLI")

    UpdateElementStyle(author, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#4a5a8a")
    UpdateElementStyle(workflow, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(loop, $fontColor="#c9d1d9", $bgColor="#2a2a2a", $borderColor="#8b949e")
    UpdateElementStyle(github, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(copilot, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")
    UpdateElementStyle(git, $fontColor="#c9d1d9", $bgColor="#1a1a1a", $borderColor="#8b949e")

    UpdateRelStyle(author, workflow, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(workflow, loop, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(loop, github, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(loop, copilot, $textColor="#c9d1d9", $lineColor="#8b949e")
    UpdateRelStyle(loop, git, $textColor="#c9d1d9", $lineColor="#8b949e")
```
