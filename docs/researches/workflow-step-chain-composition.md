# Workflow stage composition building blocks

Date: 2026-10-27
Status: Research / design recommendation
Scope: Loop composition primitives and the [unified event-driven factory](unified-event-driven-sdlc-workflow.md)

## Recommendation

Use **Stage** as the smallest reusable executable unit and a **Workflow** definition to compose stages. Each stage has a typed context, an optional executor, an optional gate, and explicit outcome-based routes.

**Context, Agent execution, Workflow Action, Gate, and Route are parts of a Stage, not necessarily five separate stages.** A stage may run an agent, run deterministic code, or evaluate an external condition without running either.

Do not overload the existing **WorkflowBuilder**: it declares observable phases but does not execute or schedule callbacks. Keep execution composition distinct from observation. The existing `Workflow` is a user-owned Python procedure; if the definition builder's name collides with it, name the builder `Flow` instead.

## Terminology

Terms follow [GLOSSARY.md](../../GLOSSARY.md); Jenkins Declarative Pipeline ([glossary](https://www.jenkins.io/doc/book/glossary/), [syntax](https://www.jenkins.io/doc/book/pipeline/syntax/)) informs optional extensions only. Jenkins "agent" (a machine) and `input` (a human pause) are **not** adopted.

| Earlier draft | Term | Reason |
| --- | --- | --- |
| WorkflowChain / `Chain` | Workflow definition (`Workflow`) | "Chain" is not a glossary term; Jenkins calls the whole definition a Pipeline, which the glossary avoids |
| WorkflowStep / `Step` | **Stage** | Glossary Stage is one factory activity that may run agents and gates; "Deterministic step" is code-only |
| Input / `.input()` | **Context** / `.context()` | Jenkins `input` means a human pause |
| AgentCall / `response=` | **Agent execution** by an **Agent role**; `envelope=` | Agent role is independent of profile; the validated response is an Envelope |
| Action | **Workflow Action** | Not an Agent Tool |
| retry / `on_retry` | **Rework loop** via `on_fail(rework(...))` | Gates decide pass, fail, or hold; retry is what a failed gate routes to |
| `Handoff.event(...)` | `emit(Event)` | An Event is an immutable fact, not a transfer of context |
| StepExecution | **Stage attempt** | Distinct from a Workflow run and an Agent run |

## Vocabulary and contracts

| Concept | Contract | Responsibility |
| --- | --- | --- |
| Stage | Stable ID, context, executor, gate, routes, policy | One independently identifiable factory activity |
| Context | Validated schema and source references | Select data and artifacts needed by a stage |
| Agent execution | Agent role, Agent client/profile, request mapping, Envelope decoder | Run a headless agent and decode its Envelope |
| Workflow Action | Python callable/service | Execute deterministic work, including GitHub/Git/tests |
| Gate | Evidence/policy predicate returning pass, fail, or hold | Decide whether work can advance |
| Route | Gate outcome mapped to a target | Choose the next stage, a Rework loop, a Handoff, an emitted Event, or stop |
| Handoff | Target and durable Envelope | Transfer responsibility to another stage, agent, or human |
| Workflow definition | Named stages and route declarations | Define an ordered workflow with bounded Rework loops |
| WorkflowRuntime | Execution state and scheduling policy | Invoke eligible stages, record attempts, resume, and coordinate handoffs |
| Stage attempt | Stage ID, attempt number, status, context/output refs, evidence | Record one attempt, separate from the immutable Stage definition |

**Gate outcomes:** pass, fail, hold. Fail routes to a bounded **Rework loop** or stops; exhausting `max_attempts` is a terminal failure or escalation. Hold is a **Human gate** or external-event wait; it is not implicit success. Failure never silently advances. A stage without a route for an outcome stops the workflow.

**Route targets:** the next stage (the default on pass), an earlier stage via Rework loop, another workflow through a **Command**, a published **Event**, or a human through a ticket/comment/approval surface (**Human handoff**). A handoff transfers durable context, not an agent's conversation memory.

## Stage lifecycle

~~~mermaid
flowchart TD
    A["Validate context"] --> B["Agent execution / Workflow Action / none"]
    B --> C{"Gate"}
    C -->|Pass| D["Next stage / emit Event"]
    C -->|Fail| G{"Rework budget left?"}
    G -->|Yes| H["Rework loop to target stage"]
    G -->|No| F["Stop or escalate"]
    C -->|Hold| E["Human gate / wait for event"]
~~~

A standalone gate stage is valid: evaluate the latest CI checks, for example, without an agent or preceding action. A stage with no gate can still require Envelope validation before handoff.

## Proposed Python API

Conceptual API, **not existing Loop code**:

~~~python
workflow = (
    Workflow("ticket-delivery")
    .context(TicketContext)
    .stage(
        Stage("implement")
        .agent(developer, envelope=ImplementationResult)
        .gate(commit_valid)
        .on_fail(rework(max_attempts=2))
        .on_hold(handoff_to_human("ticket"))
    )
    .stage(
        Stage("verify")
        .action(run_tests)
        .gate(tests_passed)
        .on_fail(rework("implement", max_attempts=2))
    )
    .stage(
        Stage("review")
        .agent(reviewer, envelope=ReviewResult)
        .gate(review_approved)
        .on_fail(rework("implement", max_attempts=2))
        .on_pass(emit(ReviewCompleted))
    )
)
~~~

Pass falls through to the next stage in declaration order; only exceptions are declared. `rework()` without a target re-runs the same stage. The example expresses ordering and outcome routing, not automatic retries from arbitrary exceptions. Runtime must distinguish executor failures, gate rejections, blocked context, and unresolved external effects.

Optional Jenkins-style extensions, added only when needed: `.when(predicate)` to skip a stage, `.after(always=..., failure=...)` for cleanup or notification actions, `.parallel(...)` for Fan-out / fan-in, `.timeout(...)` per stage, and `.trigger(...)` for what starts the workflow.

A more explicit internal contract could be:

~~~python
@dataclass(frozen=True)
class StageDefinition[ContextT, OutputT]:
    id: str
    execute: Callable[[ContextT], Awaitable[OutputT]] | None
    gate: Gate[ContextT, OutputT] | None
    routes: Mapping[Decision, Route]
    policy: StagePolicy
~~~

Context/output schemas may use normal Python dataclasses or validation models. No mandatory new dependency is needed for the first version. An agent's raw CLI result must be decoded and validated as an Envelope before use as a typed stage output.

## Constraints

These constraints apply **even if skipping them appears faster or simpler**.

| Constraint | Rationale |
| --- | --- |
| MUST give every stage a stable ID | Identify attempts, checkpoints, telemetry, and resumption |
| MUST validate context and Envelope contracts | Prevent malformed agent output from controlling routes |
| MUST separate Agent execution from Workflow Action | Preserve the Agent Tool versus Workflow Action boundary |
| MUST evaluate gates through trusted workflow code | An agent must not self-certify acceptance |
| MUST distinguish definition from Stage attempt | One stage can run many times without changing its definition |
| MUST encode pass/fail/hold routes explicitly | Avoid hidden control flow or accidental advancement |
| MUST bound Rework loops | Prevent unbounded loops and cost |
| MUST persist or reconcile externally visible handoffs/effects | Avoid duplicate PRs, comments, commits, or ticket updates after interruption |
| MUST bind gate evidence to the relevant context/spec/commit revision | Stale checks and approvals must not approve newer changes |
| MUST NOT rely on agent conversation memory for handoffs | Independent sessions and resumed workflows need durable context |
| MUST NOT require an agent in every stage | Tests, CI checks, publishing, and routing are deterministic work |
| MUST NOT equate a passing process exit with a passing gate | Agent success is not behavioral verification |
| MUST NOT schedule held or dependency-blocked stages | Preserve human and native ticket dependency controls |

A Gate is a **control decision**. Loop's existing **Phase.gate** records a result for reporting; it is not by itself a scheduler, transition, or durable policy engine.

## Factory handoff and recovery

A same-process workflow may call the next stage directly. The event-driven factory must also support a durable boundary:

1. Record stage result, evidence references, decision, and expected work-item revision.
2. Persist the intended handoff and a checkpoint before or atomically with the corresponding local state transition.
3. Dispatch the next Command, publish the Event, or publish a ticket comment and await explicit human release.
4. On restart, reconcile the intended handoff with actual GitHub/Git state before replaying effects.

Queue/delivery durability is a separate transport decision. A handoff contract should work with either an in-process dispatcher plus persisted state or a durable SQLite delivery table. It must never claim exactly-once external effects without reconciliation.

## Composition examples

| Workflow | Stage | Executor | Gate | Route |
| --- | --- | --- | --- | --- |
| Ticket delivery | Implement | Developer agent | Commit and scope valid | Verify |
| Ticket delivery | Verify | Workflow Action: tests / Verifier | Required evidence passes | Review, else Rework loop to Implement |
| PR babysitting | Inspect review | Address agent | Findings decoded and actionable | Developer fix or ready check |
| PR readiness | Check CI/approvals | GitHub Workflow Action | Required checks and review policy (Merge gate) | Merge eligibility or hold |
| Clarification | Request input | TicketTracker Workflow Action | Blocking question published | Human handoff; answer arrives as an Event |
| Post-merge | Monitor rollout | Workflow Action / Monitor | Health and deployment checks | Delivered or incident workflow |

A **Workflow definition** with default fall-through and bounded Rework loops is sufficient for straight-line execution. Add parallel stages (Fan-out / fan-in) and wait-for-event only when necessary for concurrency and long-running event resumption. Do not introduce a general DAG engine as the initial requirement.

## Design options

| Option | Benefits | Costs | Fit |
| --- | --- | --- | --- |
| **Composite Stage (recommended)** | Small API, typed contracts, reusable gates, explicit routing | Needs route validation | Current Loop/factory workflows |
| Separate graph node types for context/agent/gate/handoff | Arbitrary branching and visual editing | More boilerplate and graph-runtime complexity | Future visual workflow designer |
| Plain Python functions only | Minimal implementation | No consistent stage records, validation, or handoffs | Tiny disposable scripts |

## Placement in Loop

- **Loop library:** Existing AgentClient/AgentRequest/AgentResult, Git, hooks, observation; optionally generic Stage definitions and context/output helpers.
- **Factory workflow:** Stage composition, business policies, routing, ticket/PR semantics, human decisions, event handoffs, and durable work-item state.
- **Runtime:** Executes eligible stages, enforces routes and Rework-loop bounds, and coordinates checkpointing; keep it factory-owned initially.
- **Existing WorkflowBuilder:** Continue to provide optional phase reporting. A running Stage may open reporting Phases, but a Phase must not silently become an executable Stage.

A routing DSL in the library reverses the glossary position that Loop is not a workflow engine (see [ship-loop-as-a-workflow-library-with-no-built-in-workflows](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)); that needs an ADR, or the DSL stays in the factory layer.

This respects Loop's established separation: user-owned Python workflows control external effects; the library supplies reusable agent execution and observation primitives.

## Sources

- [Loop WorkflowBuilder](https://github.com/antshc/loop/blob/main/src/loop/workflow/builder.py)
- [Loop Workflow and WorkflowRun](https://github.com/antshc/loop/blob/main/src/loop/workflow/workflow.py)
- [Loop Phase and Phase.gate](https://github.com/antshc/loop/blob/main/src/loop/workflow/phases.py)
- [Loop Glossary](https://github.com/antshc/loop/blob/main/GLOSSARY.md)
- [Jenkins glossary](https://www.jenkins.io/doc/book/glossary/) and [Declarative Pipeline syntax](https://www.jenkins.io/doc/book/pipeline/syntax/)
- [Unified event-driven SDLC plan](https://github.com/antshc/loop/blob/main/docs/researches/unified-event-driven-sdlc-workflow.md)
- [Local event-driven messaging research](https://github.com/antshc/loop/blob/main/docs/researches/local-event-driven-messaging.md)
