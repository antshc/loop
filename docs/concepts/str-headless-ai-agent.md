# Headless AI Agent
**Type:** Architecture Pattern

## Purpose

Define an AI coding agent that can run without an interactive chat UI so it can be invoked, isolated, observed, and composed by automation such as Orb and Ralph loops.

## Concept

A Headless AI Agent is a non-interactive agent process started programmatically with a task prompt, repository/worktree, tools, and execution constraints. It performs a bounded unit of work and returns machine-observable output and process status.

The orchestration layer owns when and where the agent runs. The agent owns the task execution inside the supplied workspace. Durable state belongs in the repository, task system, or other external stores rather than in an interactive conversation.

## Rules

- MUST be invokable non-interactively from a CLI, process API, or equivalent automation boundary.
- MUST accept the task and required execution context without requiring interactive user input during normal execution.
- MUST operate only inside the repository/worktree and external systems explicitly provided to the invocation.
- MUST expose machine-observable completion through process status and/or structured output.
- MUST keep conversational session state disposable; durable progress MUST be persisted externally.
- MUST support bounded execution so an orchestrator can enforce iteration, timeout, cancellation, or task limits.
- MUST NOT require a graphical or chat UI to complete its assigned task.
- SHOULD emit logs or events sufficient for the orchestrator to diagnose execution and collect results.

## Example

```python
import subprocess

def run_headless_agent(worktree: str, prompt: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["copilot", "-p", prompt],
        cwd=worktree,
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
    )

result = run_headless_agent(
    worktree="/tmp/orb/task-42",
    prompt="Implement issue #42, verify it, commit the result, then exit.",
)

if result.returncode != 0:
    raise RuntimeError(result.stderr)
```

Orb can create the worktree and invoke the agent; a Ralph option can decide when to invoke it again in a fresh session.

## Benefits and Trade-offs

**Benefits**

- Works naturally with CI, schedulers, loops, and multi-agent orchestration.
- Separates orchestration from agent execution.
- Makes isolated worktrees and disposable sessions practical.
- Enables deterministic process-level timeout, cancellation, and result handling.

**Trade-offs**

- Interactive clarification is unavailable unless modeled explicitly through external state or escalation.
- The prompt and supplied repository/task state must contain enough context for autonomous execution.

## Validation

A conforming agent can be launched from automation against a supplied worktree, complete without interactive UI input, emit observable results, terminate, and leave durable progress outside its conversational session.
