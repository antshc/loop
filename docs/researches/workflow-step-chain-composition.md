# Workflow chain building blocks

Date: 2026-10-27
Status: Research / design recommendation
Scope: Loop composition primitives and the [unified event-driven factory](../plans/unified-event-driven-sdlc-workflow.md)

## Recommendation

Use **WorkflowStep** as the smallest reusable executable unit and **WorkflowChain** to compose steps. Each step has a typed input, an optional executor, optional gates, and explicit outcome-based handoffs.

**Input, AgentCall, Action, Gate, and Handoff are parts of a Step, not necessarily five separate steps.** A step may invoke an agent, run deterministic code, or evaluate an external condition without invoking either.

Do not overload the existing **WorkflowBuilder**: it declares observable phases but does not execute or schedule callbacks. Keep execution composition distinct from observation.

## Vocabulary and contracts

| Concept | Contract | Responsibility |
| --- | --- | --- |
| WorkflowStep | Stable ID, input, executor, gate, transitions, policy | One independently identifiable unit of work |
| Input | Validated schema and source references | Select data/context/artifacts needed by a step |
| AgentCall | Agent client/profile, request mapping, response decoder | Invoke a headless agent and decode its result |
| Action | Python callable/service | Execute deterministic work, including GitHub/Git/tests |
| Gate | Evidence/policy predicate returning a decision | Decide whether work can advance |
| Handoff | Target and durable output envelope | Transfer responsibility to another step, workflow, or human |
| WorkflowChain | Named steps and transition declarations | Define an ordered workflow with bounded rework branches |
| WorkflowRuntime | Execution state and scheduling policy | Invoke eligible steps, record attempts, resume, and coordinate handoffs |
| StepExecution | Step ID, attempt, status, input/output refs, evidence | Record one attempt, separate from the immutable Step definition |

**Gate outcomes:** pass, retry/rework, hold, fail. Retry must have a bound. Hold means wait for a human or external event; it is not implicit success. Failure never silently advances.

**Handoff targets:** another step in the same chain, another workflow through a command/event, or a human through a ticket/comment/approval surface. A handoff transfers durable context, not an agent's conversation memory.

## Step lifecycle

~~~mermaid
flowchart TD
    A["Validate input"] --> B["AgentCall / Action / none"]
    B --> C{"Gate"}
    C -->|Pass| D["Handoff to next"]
    C -->|Retry| B
    C -->|Hold| E["Wait for human/event"]
    C -->|Fail| F["Stop or escalate"]
~~~

A standalone gate step is valid: evaluate the latest CI checks, for example, without an agent or preceding action. A step with no gate can still require output-contract validation before handoff.

## Proposed Python API

Conceptual API, **not existing Loop code**:

~~~python
workflow = (
    Chain("ticket-delivery")
    .input(TicketContext)
    .step(
        Step("implement")
        .agent(developer, response=ImplementationResult)
        .gate(commit_valid)
        .on_pass("verify")
        .on_retry("implement", max_attempts=2)
        .on_hold(Handoff.human("ticket"))
    )
    .step(
        Step("verify")
        .action(run_tests)
        .gate(tests_passed)
        .on_pass("review")
        .on_retry("implement", max_attempts=2)
    )
    .step(
        Step("review")
        .agent(reviewer, response=ReviewResult)
        .gate(review_approved)
        .on_pass(Handoff.event("ReviewCompleted"))
        .on_retry("implement", max_attempts=2)
    )
)
~~~

The example expresses ordering and outcome routing, not automatic retries from arbitrary exceptions. Runtime must distinguish executor failures, gate rejections, blocked input, and unresolved external effects.

A more explicit internal contract could be:

~~~python
@dataclass(frozen=True)
class StepDefinition[InputT, OutputT]:
    id: str
    execute: Callable[[InputT], Awaitable[OutputT]] | None
    gate: Gate[InputT, OutputT] | None
    routes: Mapping[Decision, Handoff]
    policy: StepPolicy
~~~

Input/output schemas may use normal Python dataclasses or validation models. No mandatory new dependency is needed for the first version. An agent's raw CLI result must be decoded and validated before use as a typed step output.

## Constraints

These constraints apply **even if skipping them appears faster or simpler**.

| Constraint | Rationale |
| --- | --- |
| MUST give every step a stable ID | Identify retries, checkpoints, telemetry, and resumption |
| MUST validate input/output contracts | Prevent malformed agent output from controlling transitions |
| MUST separate AgentCall from Action | Preserve the agent-tool versus workflow-action boundary |
| MUST evaluate gates through trusted workflow code | An agent must not self-certify acceptance |
| MUST distinguish definition from execution attempt | One step can run many times without changing its definition |
| MUST encode pass/retry/hold/fail transitions explicitly | Avoid hidden control flow or accidental advancement |
| MUST bound retries and rework cycles | Prevent unbounded loops and cost |
| MUST persist or reconcile externally visible handoffs/effects | Avoid duplicate PRs, comments, commits, or ticket updates after interruption |
| MUST bind gate evidence to the relevant input/spec/commit revision | Stale checks and approvals must not approve newer changes |
| MUST NOT rely on agent conversation memory for handoffs | Independent sessions and resumed workflows need durable context |
| MUST NOT require an agent in every step | Tests, CI checks, publishing, and routing are deterministic work |
| MUST NOT equate a passing process exit with a passing gate | Agent success is not behavioral verification |
| MUST NOT schedule held or dependency-blocked steps | Preserve human and native ticket dependency controls |

A Gate is a **control decision**. Loop's existing **Phase.gate** records a result for reporting; it is not by itself a scheduler, transition, or durable policy engine.

## Factory handoff and recovery

A same-process chain may call the next step directly. The event-driven factory must also support a durable boundary:

1. Record step result, evidence references, decision, and expected work-item revision.
2. Persist the intended handoff and a checkpoint before or atomically with the corresponding local state transition.
3. Dispatch the next command/event, or publish a ticket comment and await explicit human release.
4. On restart, reconcile the intended handoff with actual GitHub/Git state before replaying effects.

Queue/delivery durability is a separate transport decision. A handoff contract should work with either an in-process dispatcher plus persisted state or a durable SQLite delivery table. It must never claim exactly-once external effects without reconciliation.

## Composition examples

| Workflow | Step | Executor | Gate | Handoff |
| --- | --- | --- | --- | --- |
| Ticket delivery | Implement | Developer agent | Commit and scope valid | Verify |
| Ticket delivery | Verify | Deterministic tests / verifier | Required evidence passes | Review, else rework |
| PR babysitting | Inspect review | Address agent | Findings decoded and actionable | Developer fix or ready check |
| PR readiness | Check CI/approvals | GitHub Action | Required checks and review policy | Merge eligibility or hold |
| Clarification | Request input | TicketTracker Action | Blocking question published | Human answer event |
| Post-merge | Monitor rollout | Action/monitor | Health and deployment checks | Delivered or incident workflow |

A **Chain** is sufficient for straight-line execution and bounded rework. Add **Parallel**, **Join**, and **WaitForEvent** only when necessary for fan-out/fan-in and long-running event resumption. Do not introduce a general DAG engine as the initial requirement.

## Design options

| Option | Benefits | Costs | Fit |
| --- | --- | --- | --- |
| **Composite WorkflowStep (recommended)** | Small API, typed contracts, reusable gates, explicit routing | Needs transition validation | Current Loop/factory workflows |
| Separate graph node types for input/agent/gate/handoff | Arbitrary branching and visual editing | More boilerplate and graph-runtime complexity | Future visual workflow designer |
| Plain Python functions only | Minimal implementation | No consistent step records, validation, or handoffs | Tiny disposable scripts |

## Placement in Loop

- **Loop library:** Existing AgentClient/AgentRequest/AgentResult, Git, hooks, observation; optionally generic Step definitions and input/output helpers.
- **Factory workflow:** Step composition, business policies, routing, ticket/PR semantics, human decisions, event handoffs, and durable work-item state.
- **Runtime:** Executes eligible steps, enforces transitions/retries, and coordinates checkpointing; keep it factory-owned initially.
- **Existing WorkflowBuilder:** Continue to provide optional phase reporting. A running Step may open a reporting Phase, but a Phase must not silently become an executable Step.

This respects Loop's established separation: user-owned Python workflows control external effects; the library supplies reusable agent execution and observation primitives.

## Sources

- [Loop WorkflowBuilder](https://github.com/antshc/loop/blob/main/src/loop/workflow/builder.py)
- [Loop Workflow and WorkflowRun](https://github.com/antshc/loop/blob/main/src/loop/workflow/workflow.py)
- [Loop Phase and Phase.gate](https://github.com/antshc/loop/blob/main/src/loop/workflow/phases.py)
- [Loop Glossary](https://github.com/antshc/loop/blob/main/GLOSSARY.md)
- [Unified event-driven SDLC plan](https://github.com/antshc/loop/blob/main/docs/plans/unified-event-driven-sdlc-workflow.md)
- [Local event-driven messaging research](https://github.com/antshc/loop/blob/main/docs/researches/local-event-driven-messaging.md)
