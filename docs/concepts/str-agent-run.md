# Agent Run
**Type:** Architecture Pattern

## Purpose

Define Loop's smallest agent-execution unit so workflows can compose planner, implementer, reviewer, or other agent steps without owning provider-specific execution mechanics.

## Concept

An **Agent Run** is one bounded invocation of one Headless AI Agent with one request on one prepared worktree.

The library owns the Run boundary: an `AgentClient` built for one `AgentProfile` (CLI, model, reasoning effort) takes an `AgentRequest(prompt)`, runs the agent CLI on the host in the worktree, and returns one `AgentResult` with the raw output. A Workflow owns topology by composing Runs, usually as the agent executors of Stages in a declarative definition run by Loop's Workflow runtime (sequence, per-item loop, switch, workspace scope). A Run does not encode workflow roles such as planner, implementer, or reviewer.

## Rules

- MUST treat one Agent Run as the smallest executable runtime unit: one agent profile, one prompt, one worktree, and one result.
- MUST obtain the client from the builder (`Agent().create()`) or from an opened `Worktree` (`worktree.agent(profile)`) before invoking the agent.
- MUST invoke the agent provider through the `AgentClient` contract.
- MUST choose the agent per client through its `AgentProfile`; Runs on one worktree MAY use different profiles ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- MUST return the agent CLI's raw output; a Workflow that expects a response envelope extracts and validates it itself.
- MUST keep workflow topology in Workflow code rather than in the Run abstraction.
- MUST let a Workflow decide subsequent Runs from prior Run results and external state.
- MUST keep a Run prompt scoped to that Run's agent task rather than workflow orchestration.
- MUST compose Workflows from Runs through the Workflow's own code or Loop's declarative runtime; a Run never schedules another Run ([Compose Workflows as declarative definitions](../adr/compose-factory-workflows-as-declarative-definitions-over-a-stages-library-run-by-a-factory-runtime.md)).
- MUST NOT encode planner, implementer, reviewer, or other workflow-specific roles into the Run contract.

### Variant: Shared Worktree
**Selected when:** multiple Runs operate on the same evolving workspace and later Runs depend on changes or results produced by earlier Runs.

- MUST open one `Worktree` and take every dependent Run's client from it.
- MUST preserve each Run as a separate agent invocation with its own prompt and result.
- MAY continue one conversation across Runs by enabling `with_session()` and reusing the session name.
- SHOULD execute dependent Runs sequentially in workflow-defined order.

### Variant: Separate Worktrees
**Selected when:** Runs operate on independent units of work that do not require a shared mutable workspace.

- MUST open a separate `Worktree` for each independent unit of work.
- MUST keep mutable workspace state separate between those worktrees.
- MAY execute independent Runs concurrently when the Workflow allows it.

## Example

Shared worktree:

```text
with builder.open() as worktree:
  ├─ worktree.agent(plan_profile).run(AgentRequest(prompt))
  └─ worktree.agent(implement_profile).run(AgentRequest(prompt))
```

Separate worktrees:

```text
Workflow
  ├─ open() on branch A → run(issue 1)
  ├─ open() on branch B → run(issue 2)
  └─ open() on branch C → run(issue 3)
```

The Workflow selects the variant, sequence, conditions, and concurrency. Each Run remains the same execution primitive.

## Benefits and Trade-offs

**Benefits**

- Workflows stay explicit and readable because orchestration is a short declarative definition.
- Agent-provider details stay behind one library boundary.
- New workflow shapes can reuse the same Run primitive without expanding the library contract.

**Trade-offs**

- Workflows depend on the runtime's definition language; what its four constructs cannot express needs an escape hatch.
- State shared between Runs must be explicit in the worktree, Run results, or external durable state.

## Validation

The `plan_implement` Workflow runs a planning Run and an implementation Run with different profiles on one worktree scope; the `implement_spec` Workflow runs one Run per Ticket and keeps repeated invocation policy in its definition and policy.

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Headless AI Agent](str-headless-ai-agent.md)
- [Harness Repository Topology](str-harness-repository-topology.md)

## Implementation Map

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Agent invocation | One request crosses the library agent boundary and returns one result | `loop`: `AgentClient`, `AgentRequest`, `AgentResult` |
| Worktree binding | A worktree hands out clients per profile | `loop`: `AgentBuilder.open`, `Worktree.agent` |
| Workflow ownership | Workflow definitions decide repeated and conditional agent execution | `workflows.software_factory.workflows.implement_spec`, `workflows.loop.plan_implement` |
