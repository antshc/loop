# Ralph Wiggum Loop
**Type:** Architecture Pattern

## Purpose

Provide a reusable autonomous development-loop pattern that can continue across bounded AI coding-agent sessions without accumulating degraded conversational context. The pattern defines the invariants; concrete orchestration strategies are selectable options.

## Concept

A Ralph Wiggum loop repeatedly starts fresh AI coding-agent sessions against durable external state. Each iteration reconstructs the current task state from artifacts such as issues, a task list, PRD, source tree, Git history, or test results, performs bounded work, then persists progress for the next iteration.

The orchestration strategy is replaceable. Every option MUST preserve fresh sessions, external state, bounded progress, durable handoff, and verifiable completion.

## Options

### Simple Loop

Use a small shell or Python loop that repeatedly invokes one fresh agent session with the same prompt. Each iteration reconstructs current state, performs one bounded unit of work, verifies it, persists the result, and exits. The outer loop starts the next fresh session.

Use this as the default when work can be serialized and does not require planner/reviewer or parallel-agent coordination.

#### Rules

- MUST use a simple iteration loop that repeatedly invokes the coding agent until completion or an explicit iteration/stop limit; it MUST NOT require planner/reviewer orchestration.
- MUST start each iteration as a fresh agent invocation without conversational context from the previous iteration.
- MUST use the same stable prompt or task contract for every iteration.
- MUST refresh external state at the start of every iteration, including the actionable task list and relevant repository history/state.
- MUST work on one bounded task per iteration.
- MUST persist progress through durable external state such as issues, commits, source files, and test results so the next fresh iteration can reconstruct progress.
- MUST run explicit binary verification, such as tests, type checks, or a build, before marking the selected task complete.
- MUST persist the completed task and handoff state before the iteration exits, for example by committing changes and closing/updating the task.
- MUST terminate only when refreshed external state shows no actionable work remains, all remaining work is explicitly blocked, or another explicit stop condition applies.
- MUST NOT treat agent self-assessment alone as completion evidence.

```python
import subprocess

MAX_ITERATIONS = 10
PROMPT = """
Work on the next actionable issue.
Read current task and repository state first.
Complete one issue only.
Run verification before committing.
Persist progress before exiting.
"""

def run_agent() -> int:
    return subprocess.run(
        ["copilot", "-p", PROMPT],
        check=False,
    ).returncode

def has_actionable_tasks() -> bool:
    return subprocess.run(
        ["python", "scripts/has_open_tasks.py"],
        check=False,
    ).returncode == 0

def verification_passes() -> bool:
    return subprocess.run(
        ["python", "-m", "pytest"],
        check=False,
    ).returncode == 0

for _ in range(MAX_ITERATIONS):
    if not has_actionable_tasks():
        break

    run_agent()  # new process = fresh agent session

    if not verification_passes():
        continue

if has_actionable_tasks():
    raise SystemExit("Loop stopped before completion")
```

Additional orchestration options may be added as sibling sections under **Options** when they preserve the core loop invariants but solve different coordination needs.

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
