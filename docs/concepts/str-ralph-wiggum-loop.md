# Ralph Wiggum Loop
**Type:** Architecture Pattern

## Purpose

Provide a reusable autonomous development-loop pattern that can continue across bounded AI coding-agent sessions without accumulating degraded conversational context. The pattern defines the invariants; concrete orchestration strategies are selectable options.

## Concept

A Ralph Wiggum loop repeatedly starts fresh AI coding-agent sessions against durable external state. Each iteration reconstructs the current task state from artifacts such as issues, a task list, PRD, source tree, Git history, or test results, performs bounded work, then persists progress for the next iteration.

The orchestration strategy is replaceable. Every option MUST preserve fresh sessions, external state, bounded progress, durable handoff, and verifiable completion.

## Options

### Simple Loop

Repeatedly invoke one coding agent with a stable prompt. Each iteration is fresh, rebuilds current external state, completes one issue, verifies it, commits the result, and closes the issue. No planner/reviewer orchestration.

#### Rules

- MUST use a simple bounded loop, such as `max_iterations = 3`, to repeatedly invoke the coding agent.
- MUST start a new agent invocation for every iteration; previous conversational context MUST NOT be resumed.
- MUST load the same stable prompt file for every iteration.
- MUST rebuild dynamic prompt state every iteration:
  - current actionable issues from the task source;
  - recent `RALPH:` commits from Git history.
- MUST instruct the agent to work on exactly one issue per iteration.
- MUST keep cross-iteration state in GitHub issues, Git commits, source files, and tests rather than previous chat context.
- MUST run the configured verification commands before committing or closing the issue.
- MUST commit completed work with the `RALPH:` prefix and close the completed issue so the next fresh iteration sees the updated task list and history.
- MUST stop early when the agent emits `<promise>COMPLETE</promise>`, which means no actionable issues remain.
- MUST stop when the iteration limit is reached even if completion was not signaled.

#### Python example

```python
from pathlib import Path
import subprocess

MAX_ITERATIONS = 3
PROMPT_FILE = Path(".shipyard/prompt.md")
COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"

def sh(*args: str) -> str:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

def build_prompt() -> str:
    template = PROMPT_FILE.read_text()

    # Re-evaluated every iteration.
    open_issues = sh("python", "scripts/list_tasks.py")
    recent_ralph_commits = sh(
        "git", "log", "--oneline", "--grep=RALPH", "-10"
    )

    return (
        template
        .replace("{{LIST_TASKS_COMMAND}}", open_issues)
        .replace(
            '{{GIT_HISTORY}}',
            recent_ralph_commits,
        )
    )

def run_agent(prompt: str) -> str:
    # New process => fresh agent session.
    result = subprocess.run(
        ["copilot", "-p", prompt],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout

for _ in range(MAX_ITERATIONS):
    prompt = build_prompt()
    output = run_agent(prompt)

    if COMPLETION_SIGNAL in output:
        break
```

The stable prompt carries the per-iteration workflow:

```text
# Context

## Open issues
{{LIST_TASKS_COMMAND}}

## Recent RALPH commits
{{GIT_HISTORY}}

# Task

Work on one issue only.

Before commit:
1. npm run typecheck
2. npm run test

When complete:
1. git commit with a message starting with "RALPH:"
2. close the issue

If no actionable issues remain, output:
<promise>COMPLETE</promise>
```

Additional orchestration options may be added as sibling sections under **Options**.

## Benefits and Trade-offs

**Benefits**

- Fresh context reduces context rot, drift, and accumulation of incorrect assumptions.
- GitHub issues and Git commits make progress resumable across independent runs.
- Verification gates keep issue completion externally checkable.
- The loop stays small and understandable.

**Trade-offs**

- Fresh sessions may spend tokens rediscovering repository and task context.
- Simple Loop serializes work.
- Correctness depends on the prompt and task-list command exposing enough durable state.

## Validation

A conforming Simple Loop demonstrates that each iteration launches a new agent process, rebuilds current task and Git state, handles one issue, verifies before commit/close, persists progress through Git and the issue tracker, and terminates on `<promise>COMPLETE</promise>` or the iteration limit.
