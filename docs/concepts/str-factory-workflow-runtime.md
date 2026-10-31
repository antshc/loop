# Factory Workflow Runtime
**Type:** Architecture Pattern

## Purpose

Let every Workflow (the factory's `implement_spec`, `pr_babysit` and `address_once`, and the `plan_implement` example) be written as one short declarative definition over a library of Stages, so that rework, repository contracts, hand-off, resume after a crash, and reporting are implemented and tested once, and a structural mistake fails when the definition is built, not mid-run. The runtime is part of the `loop` library (`loop.runtime`, exported from top-level `loop`) and is Loop's one Workflow architecture ([Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md), [decision](../adr/compose-factory-workflows-as-declarative-definitions-over-a-stages-library-run-by-a-factory-runtime.md)).

## Concept

A **Workflow definition** names its context (the work item) and places Stages with four constructs; the runtime executes it for one work item.

- **Stage.** An immutable value with a kebab-case id, a typed frozen-dataclass input, the Event it produces, at most one executor, a trusted gate, a repository contract and Routes. Every modifier (`on_pass`, `on_fail`, `on_hold`, `tune`, `named`, `when`, `once`, `limit`, `reads`) returns a modified copy, so a library default is never mutated by a Workflow.
  - *Executor:* a role agent in the enclosing workspace, a deterministic **Action** (no external effect, re-run freely), or an **Effect** (an externally visible effect with a `reconcile` that recognizes what a crashed attempt already did). A Stage with no executor is a pure gate over what was produced before.
  - *Gate:* the only place that decides. It reads the executor's reply, the input and the Git **Evidence** the runtime collected, and returns pass, fail or hold. An agent's own success statement never passes a Stage.
  - *Repository contract:* no repository, read-only (HEAD and status unchanged), agent commits (exactly one commit with the expected subject prefix, a clean tree, reported commit equal to HEAD), or harness commits (the agent never moves HEAD; after verification the runtime commits once if anything changed). A failed attempt is restored to its base unless the contract retains the tree for inspection.
- **Four constructs, no graph.** `stage(s)` is a sequence; `each(selector, body)` runs a body per item and re-selects after each one; `dispatch(ClosedSum, {Variant: flow_or_route})` is an exhaustive switch; `within(opener, body)` is a workspace scope. A **Flow** fragment (`then(fragment)`) only splices nodes and adds no scope. A **Selector** is pure: given the scope's facts it offers the candidate items, possibly without end, never an effect; the loop takes the first item it has not run.
- **Routes.** After a pass, fail or hold a Stage takes its route: continue (`next_()`), end the item (`next_item()`), re-run the same Stage (`rework(max_attempts, then=...)`), stop the scope (`stop.completed/idle/held/failed`), hand off (`handoff(stage)`: run an effect or action Stage once, then end the scope handed off to a person) or jump to a sibling `dispatch` case (`reroute`). Pass defaults to the next node; fail stops failed and hold stops held, so a failure never advances silently. Rework re-runs only the same Stage; there are no backward or forward jumps.
- **Event resolution.** A Stage's input fields resolve by their type, not by name or wiring: the latest Event, an optional Event, `All[T]` (every T recorded in this run), `History[T]` (every T recorded for the work item across runs), the work item context, the current loop item, the agent workspace, or a service. A field that cannot resolve, or an Event with several upstream producers and no `.reads(T, from_=...)`, is rejected when the definition is built. `explain()` prints which Stage feeds which field.
- **Execution ledger.** The runtime alone writes one append-only, versioned JSONL log per work item (`<root>/<workflow>/<slug>-<sha256(item)[:12]>/log.jsonl`), under one lock per work item. It records the run and its epoch, each scope, and each attempt as it moves from running to executed, committing and passed, failed or held, together with the produced Events and check outcomes. An unfinished scope continues, a finished Stage replays its recorded outcome for the same work key, and an interrupted attempt is reconciled (adopted commit, stored reply gated again on an unchanged working-tree content id, effect adopted from its target, otherwise unverified). A handed-off scope stays open across runs until intake writes a release. A loop item that completes without writing anything is forgotten: its buffered check outcomes are dropped and its Events stay out of the enclosing scope, as they would after a restart.
- **Spans.** The runtime derives run, scope and Stage attempt spans from the definition ([Run Observation](str-run-observation.md#runtime-spans)).
- **Services.** Concrete instances keyed by their exact type (`Services`), passed to `run(...)` explicitly. The definition derives the types it needs, and `run` refuses to start when one is missing; no container is involved.

### Escape hatches

Three deliberate ways to express what the constructs alone cannot; each is written in the definition, never implied.

- **Effect with an internal loop.** A single Effect Stage may loop internally (for example, replying to every feedback item), because its `reconcile` makes a repeat after a crash safe.
- **Reroute.** `reroute(Variant)` inside a `dispatch` case abandons the case and runs a sibling case. Reroutes must be acyclic, which `check()` verifies.
- **Explicit once and limit.** `.once(per=key)` allows at most one pass per key across runs (an agent or effect Stage only); `.limit(attempts, per=key)` fails a Stage without executing it once that many attempts have started for the key, an interrupted attempt counting. Both are keyed from the ledger.

### Where policy lives

- **Stages** (the factory's `workflows/software_factory/stages/`, or a Workflow's own) hold activity logic with complete defaults. Policy that varies by Workflow arrives as value or callable parameters, never booleans.
- **Workflow packages** (`workflows/software_factory/<name>/`) hold the definition, a `policy.py` of values and pure functions (the `hitl` label, hand-off texts, trust rules, commit subjects, the work item), settings, and the entry point that builds `Services`. A definition holds no Stage logic and a policy module imports no process, Git or GitHub client.
- **Events** (`workflows/software_factory/events/`) are data only.
- **The runtime** (`src/runtime/`) knows no GitHub, Ticket or pull request, never imports Loop's shared helpers or a Workflow, and starts no process; Git runs behind an injected command. Its typed agents come from Loop's typed agent contract; concrete role agents belong to the Workflow.

## Rules

- MUST write every factory Workflow as a definition over the Stage library; it MUST NOT hand-write rework, repository validation, hand-off, resume or span logic.
- MUST give each Stage a typed frozen-dataclass input and a single declared Event (or closed sum) it produces; a Stage MUST NOT read an Event nothing upstream produces.
- MUST decide pass, fail or hold in the trusted gate from Evidence; an executor's own claim MUST NOT advance the Stage.
- MUST declare every route that leaves the default (rework, hand-off, reroute, once, limit) explicitly in the definition.
- MUST make an Effect idempotent through `reconcile`, so a crashed attempt's effect is adopted, not repeated.
- MUST write run state only through the Execution ledger, by the runtime; a Workflow MUST NOT keep its own per-item state file.
- MUST pass every external dependency as `Services`; no module imports a dependency-injection container.
- MUST keep the factory layers `workflows > flows > stages > events > agents | shared`, all over Loop's runtime; Stages and Workflows MUST NOT import their siblings, and the runtime engine (`loop.runtime._engine`) and the factory's agent kit (`agents/_kit`) stay private.
- MUST report Workflow runs only through the runtime's spans; the earlier Phase reporting is retired and MUST NOT be reintroduced ([decision](../adr/report-factory-runs-as-opentelemetry-spans-bypassing-loops-phase-reporting.md)).
- MUST treat a ledger written before a stored class moved as unsupported: resuming it fails with an explicit decode error, and nothing migrates or deletes it.
- SHOULD add a Stage to the library only when a Workflow places it; a shared Flow fragment exists only while two or more Workflows place it.

## Example

Sketch of one Workflow, abridged from `implement_spec`:

```python
ticket = (
    Flow("ticket")
    .stage(tickets.implement_ticket().on_fail(rework(max_attempts=2, then=escalate)))
    .stage(tickets.close_ticket())
    .stage(publish.publish_draft_pr().named("publish-ticket"))
)
workflow = (
    Workflow("implement_spec").context(SpecWork)
    .stage(specs.route_spec(targets.repositories).on_hold(block))
    .within(workspaces.feature_worktree(), Flow("deliver")
        .each(tickets.next_ready_ticket(), ticket, on_fail=next_())
        .stage(tickets.require_delivered().on_hold(stop.idle())))
    .stage(finalize.finalize(label=HITL, source=SOURCE))
)
result = run(workflow, SpecWork(spec), services=services, ledger=Ledger(log_dir / "ledger"), spans=spans)
sys.exit(result.exit_code)   # completed, idle, held → 0; handed_off, failed → 1; interrupted → 130
```

## Benefits and Trade-offs

**Benefits**

- A Workflow is a short, readable list of Stages; a third Workflow (`address_once`) needed no new Stage logic.
- Structural mistakes (an unproduced Event, an ambiguous producer, a non-exhaustive switch, an invalid rework or reroute, a missing service) are rejected before anything runs.
- Crash recovery, repository validation and reporting are written once; a Stage is tested alone with fakes and a real temporary repository.

**Trade-offs**

- Authors learn a small language with strict rules; expressing something outside the four constructs needs an escape hatch or a runtime change.
- The runtime is a core piece of code that every Workflow depends on.
- A crash after an agent's reply was recorded with an unchanged tree re-gates and commits without asking the agent again, where the earlier implementation blocked.

## Validation

- `Workflow.check()` and `explain()` run in every definition's test; the wiring table is compared to a stored golden.
- Runtime tests cover contracts, routes, budgets, the ledger and crash recovery (a run killed after each ledger record converges to the uninterrupted outcome) over real Git; Stage tests run one Stage as a one-Stage Workflow; Workflow scenarios drive the entry point over fakes ([Test Strategy](ops-test-strategy.md)).
- Import-linter enforces the layers, the independence of Stages, Workflows and role agents, the private engine and kit, that no process starts in Stages, definitions or the runtime, and that no container is imported. Structural tests under `workflows/tests/software_factory/architecture/` derive the rest from the code: every definition builds, Stage ids are unique, every Stage is used by a Workflow, no Stage factory takes a `bool`, policy modules hold values and pure functions only, and nothing uses the retired Phase-reporting names.

## Options Considered

| Option | Outcome |
|--------|---------|
| Stage kernel with Workflow code routing on a closed outcome | Rejected. Each Workflow still hand-writes loops, rework, hand-off and resume. |
| Plain scripts per Workflow | Rejected; the library's earlier plain-script model is superseded. Reuse of a Stage across Workflows and build-time validation are lost. |
| A factory-only runtime beside a plain-script library | Superseded. Two Workflow architectures; the generic runtime moved into Loop. |
| General DAG engine | Rejected. Arbitrary jumps make resume and tracing harder than four constructs need. |
| **Validated declarative definitions over a Stages library, run by a factory runtime** | **Chosen** ([decision](../adr/compose-factory-workflows-as-declarative-definitions-over-a-stages-library-run-by-a-factory-runtime.md)). |

## Implementation Map

Verify — may drift.

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Definition language | Immutable Stage, Flow and Workflow, validated as built | `src/runtime/`: `stage.py`, `definition.py`, `routes.py`, `facts.py`, `analysis.py` |
| Interpreter | `run(...)` and the private engine | `src/runtime/run.py`, `src/runtime/_engine/` |
| Ledger and spans | Execution ledger; span seam | `src/runtime/ledger.py`, `src/runtime/spans.py`, `workflows/software_factory/infrastructure/telemetry.py` |
| Stage library and fragments | Reusable Stages and Flow fragments | `workflows/software_factory/stages/`, `workflows/software_factory/flows/` |
| Workflows | Definition, policy, entry point | `workflows/software_factory/workflows/{implement_spec,pr_babysit,address_once}/`, `workflows/loop/plan_implement.py` |
| Structure checks | Layer contracts and derived tests | `pyproject.toml` (`[tool.importlinter]`), `workflows/tests/software_factory/architecture/` |
