# Agent Run
**Type:** Architecture Pattern

## Purpose

Define Loop's smallest agent-execution unit so workflows can compose planner, implementer, reviewer, or other agent steps without owning provider-specific execution mechanics.

## Concept

An **Agent Run** is one bounded invocation of one Headless AI Agent with one prompt on one prepared worktree.

The runtime owns the Run boundary: it binds the prepared execution context, runs the `AgentClient` the workflow passes for that Run through the worktree runner's executor on the host, and returns one result. A Workflow owns topology by composing Runs with ordinary Python control flow such as sequence, branching, iteration, and concurrency. A Run does not encode workflow roles such as planner, implementer, or reviewer.

## Rules

- MUST treat one Agent Run as the smallest executable runtime unit: one agent, one prompt, one worktree, and one result.
- MUST bind the Run to its prepared worktree runner before invoking the agent.
- MUST invoke the agent provider through the `AgentClient` contract.
- MUST take the agent for each Run as an argument of the Run rather than from the runner, so Runs on one worktree may use different agents ([ADR 0008](../adr/0008-run-agents-on-the-host-through-a-worktree-runner-and-pass-the-agent-to-each-run.md)).
- MUST return a machine-readable result containing execution success and agent output.
- MUST keep workflow topology in Workflow code rather than in the Run abstraction.
- MUST let a Workflow decide subsequent Runs from prior Run results and external state.
- MUST keep a Run prompt scoped to that Run's agent task rather than workflow orchestration.
- MUST compose Workflows with ordinary Python control flow rather than a separate workflow DSL.
- MUST NOT encode planner, implementer, reviewer, or other workflow-specific roles into the Run contract.

### Variant: Shared Worktree
**Selected when:** multiple Runs operate on the same evolving workspace and later Runs depend on changes or results produced by earlier Runs.

- MUST reuse one worktree runner across the dependent Runs.
- MUST preserve each Run as a separate agent invocation with its own prompt and result.
- MAY use a different agent for each Run.
- SHOULD execute dependent Runs sequentially in workflow-defined order.

### Variant: Separate Worktrees
**Selected when:** Runs operate on independent units of work that do not require a shared mutable workspace.

- MUST give each independent unit of work its own worktree runner.
- MUST keep mutable workspace state separate between those worktrees.
- MAY execute independent Runs concurrently when the Workflow allows it.

## Example

Shared worktree:

```text
WorktreeRunner
  ├─ run(planner)
  ├─ run(implementer)
  ├─ run(reviewer)
  └─ run(fixer)
```

Separate worktrees:

```text
Workflow
  ├─ Runner A → run(issue 1)
  ├─ Runner B → run(issue 2)
  └─ Runner C → run(issue 3)
```

The Workflow selects the variant, sequence, conditions, and concurrency. Each Run remains the same execution primitive.

## Benefits and Trade-offs

**Benefits**

- Workflows stay explicit and readable because orchestration is normal Python control flow.
- Agent-provider details stay behind one runtime boundary.
- New workflow shapes can reuse the same Run primitive without expanding the runtime contract.

**Trade-offs**

- Workflows may repeat some orchestration code instead of relying on a workflow engine.
- State shared between Runs must be explicit in the worktree, Run results, or external durable state.

## Validation

The runtime prototype already exposes `AgentClient` as a one-prompt-to-one-result boundary and keeps repeated invocation policy in Workflow code. Binding to a prepared worktree is a decided Loop rule enforced by `WorktreeRunner.run`.

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Headless AI Agent](str-headless-ai-agent.md)
- [Harness Repository Topology](str-harness-repository-topology.md)

## Implementation Map

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Agent invocation | One prompt crosses the runtime agent boundary and returns one result | `loop prototype`: `AgentClient`, `AgentRunResult` |
| Workflow ownership | Workflow code decides repeated and conditional agent execution | `loop prototype`: `DevCommand`, `AgentClient` |
