# Ralph Wiggum Loop
**Type:** Architecture Pattern

## Purpose

Provide a reusable autonomous development-loop pattern that can continue across bounded AI coding-agent sessions without accumulating degraded conversational context. The pattern defines the invariants; concrete orchestration strategies are selectable options.

## Concept

A Ralph Wiggum loop repeatedly starts fresh AI coding-agent sessions against durable external state. Each iteration reconstructs the current task state from artifacts such as issues, a task list, PRD, source tree, Git history, or test results, performs bounded work, then persists progress for the next iteration.

The orchestration strategy is replaceable. Every option MUST preserve the same core properties: fresh sessions, stable task contract, external state, bounded progress, and externally verifiable completion.

## Options

### Simple Loop

Use a small shell or Python loop that repeatedly invokes one fresh agent session with the same prompt. Each iteration reads current repository/task state, performs one bounded unit of work, verifies it, persists the result, and exits. The outer loop starts the next fresh session until completion criteria pass.

Use this as the default when work can be serialized and does not require planner/reviewer or parallel-agent coordination.

```python
import subprocess

MAX_ITERATIONS = 10
PROMPT = """
Work on the next actionable issue.
Read repository state before choosing work.
Complete one issue only.
Run tests before committing.
"""

def run_agent() -> int:
    return subprocess.run(
        ["copilot", "-p", PROMPT],
        check=False,
    ).returncode

def complete() -> bool:
    return subprocess.run(
        ["python", "-m", "pytest"],
        check=False,
    ).returncode == 0 and subprocess.run(
        ["python", "scripts/has_open_tasks.py"],
        check=False,
    ).returncode == 1

for _ in range(MAX_ITERATIONS):
    if complete():
        break

    run_agent()  # new process = fresh agent session

if not complete():
    raise SystemExit("Loop stopped before completion")
```

Additional orchestration options may be added as sibling sections under **Options** when they preserve the core loop invariants but solve different coordination needs.

## Rules

- MUST start every iteration in a fresh AI agent session without conversational context from previous iterations.
- MUST provide a stable task prompt or task contract across iterations.
- MUST persist cross-iteration progress in external repository or task-system state rather than agent memory.
- MUST read current external state before selecting work for an iteration.
- MUST keep each iteration bounded to a clearly defined unit of progress.
- MUST define completion using explicit, externally verifiable pass/fail criteria.
- MUST NOT treat an agent's statement that the task is complete as sufficient completion evidence.
- SHOULD use the Simple Loop option unless the workflow requires additional coordination.
- SHOULD add alternative orchestration strategies as explicit options instead of weakening the core invariants.

## Benefits and Trade-offs

**Benefits**

- Fresh context reduces context rot, drift, and accumulation of incorrect assumptions.
- External state makes progress resumable and inspectable across independent runs.
- Binary completion criteria make termination deterministic rather than subjective.
- Multiple orchestration options can evolve without changing the core pattern.

**Trade-offs**

- Fresh sessions may spend tokens rediscovering repository and task context.
- Poorly maintained external state can cause repeated or conflicting work.
- Simple Loop serializes work; more complex coordination may require another option.

## Validation

A conforming implementation demonstrates that every iteration starts a fresh agent session, reconstructs progress from shared external state, persists its progress, and checks explicit completion criteria independently of agent self-assessment.
