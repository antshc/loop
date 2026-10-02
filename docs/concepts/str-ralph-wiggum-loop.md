# Ralph Wiggum Loop
**Type:** Architecture Pattern

## Purpose

Provide a minimal autonomous development loop that can continue across bounded AI coding-agent sessions without accumulating degraded conversational context. Progress and completion are grounded in durable repository state and executable checks rather than agent memory or self-assessment.

## Concept

The Ralph Wiggum loop repeatedly starts a fresh AI coding-agent session with the same stable task prompt. Each iteration reconstructs the current task state from shared external artifacts, such as a task list, PRD, source tree, Git history, or test results, then performs the next useful work.

The loop deliberately uses simple automation, such as a shell or Python loop, instead of a multi-phase orchestration system. Iterations do not depend on conversational memory from previous runs; they communicate through external state. Completion is determined by explicit binary pass/fail criteria, such as unit tests or a build command.

## Rules

- MUST start every iteration in a fresh AI agent session without conversational context from previous iterations.
- MUST provide the same stable task prompt or task contract to every iteration.
- MUST persist cross-iteration progress in external repository state rather than agent memory.
- MUST read the shared external state before selecting the next work for an iteration.
- MUST define completion using explicit, externally verifiable pass/fail criteria.
- MUST continue iterating until the completion criteria pass or an explicit stop condition requires human intervention.
- MUST NOT treat an agent's statement that the task is complete as sufficient completion evidence.
- SHOULD keep the loop automation simple and avoid multi-phase orchestration unless the task requires it.

## Benefits and Trade-offs

**Benefits**

- Fresh context reduces context rot, drift, and accumulation of incorrect assumptions.
- External state makes progress resumable and inspectable across independent runs.
- Binary completion criteria make termination deterministic rather than subjective.
- Simple orchestration keeps the control loop understandable and easy to modify.

**Trade-offs**

- Each fresh session may spend tokens rediscovering repository and task context.
- Poorly maintained external state can cause repeated or conflicting work.
- Large tasks still require enough decomposition for one iteration to make useful bounded progress.

## Validation

A conforming loop can demonstrate that each iteration launches a new agent session, reconstructs progress from shared repository state, and evaluates explicit completion checks before terminating.
