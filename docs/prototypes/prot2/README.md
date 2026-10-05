# prot2

Library for composing agent workflows on git worktree capsules. A workflow is an ordinary Python script that imports only from `orb` and owns its own control flow.

## Component diagram

```mermaid
C4Component
    title Component diagram - orb library (prot2)

    Person(author, "Workflow author", "Writes and runs workflow scripts")

    Container(workflows, "Workflows", "Python scripts", "dev, parallel_planner, address_prs, fix_prs plus prompt templates")

    Container_Boundary(orb, "orb library") {
        Component(api, "Public API", "orb/__init__.py", "Single import surface for workflows")
        Component(runner, "Runner", "runner.py, capsule.py", "run() one-shot; Capsule runs agent iterations and merges into host")
        Component(prompt, "Prompt and tags", "prompt.py, tags.py", "Template rendering with shell expansion; tag and JSON extraction")
        Component(parallel, "Parallel and attempts", "parallel.py, attempts.py", "parallel_settled fan-out; failed-attempt limit")
        Component(contracts, "Contracts", "contracts/", "AgentClient, CapsuleProvider, CapsuleInstance, SourceControlPlatform, WorkTracker, ExecutionStore")
        Component(capsules, "Worktree capsule", "capsules/worktree.py, capsules/docker.py, worktree.py", "CapsuleProvider backed by a git worktree; host or Docker execution")
        Component(agents, "Agents", "agents/", "CopilotCliAgent and ScriptedAgent")
        Component(platforms, "Platform adapters", "platforms/", "GitHub and Azure DevOps adapters chosen from the origin remote")
        Component(store, "Execution store", "stores/file.py", "File-backed attempt record")
        Component(process, "Process", "process.py, errors.py", "Command execution and error types")
    }

    System_Ext(git, "git", "Worktrees, branches, merges")
    System_Ext(docker, "Docker", "Container runtime")
    System_Ext(copilot, "Copilot CLI", "Headless AI agent")
    System_Ext(forge, "GitHub / Azure DevOps", "Issues, pull requests, review threads via gh / az")

    Rel(author, workflows, "Runs")
    Rel(workflows, api, "Imports")
    Rel(api, runner, "Re-exports")
    Rel(api, parallel, "Re-exports")
    Rel(api, platforms, "Re-exports factories")

    Rel(runner, contracts, "Depends on")
    Rel(runner, prompt, "Renders prompts")
    Rel(runner, capsules, "Opens capsule via CapsuleProvider")
    Rel(runner, agents, "Calls AgentClient.run")
    Rel(workflows, store, "Records attempts")
    Rel(store, contracts, "Implements ExecutionStore")

    Rel(capsules, process, "Executes git")
    Rel(agents, capsules, "Runs inside CapsuleInstance")
    Rel(platforms, process, "Runs gh / az")

    Rel(capsules, git, "Uses")
    Rel(capsules, docker, "Uses")
    Rel(agents, copilot, "Invokes")
    Rel(platforms, forge, "Queries and replies")

    UpdateLayoutConfig($c4ShapeInRow="4", $c4BoundaryInRow="1")
```

## Run

```
pip install -e .
pytest
```
