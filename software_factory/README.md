# Write a Workflow

The software factory is a library of reusable Stages and a small definition language over them. A Workflow is one declarative definition, run by the factory runtime once per work item. It imports only the public `loop` package and is not part of the installed `loop` distribution.

```python
from software_factory.runtime import Workflow, Flow, rework, handoff, stop, next_
from software_factory.stages.tickets import next_ready_ticket, implement_ticket, close_ticket, escalate_ticket

deliver = (
    Flow("ticket")
    .stage(implement_ticket().on_fail(rework(max_attempts=2, then=handoff(escalate_ticket(label="hitl")))))
    .stage(close_ticket())
)
```

The rules a definition author needs:

1. **A Stage from `stages/` is one line.** Its defaults (executor, gate, work contract, routes) are complete. Call `.on_pass/.on_fail/.on_hold`, `.tune(...)` or `.named(...)` only for what differs.
2. **Pass falls through to the next Stage.** Fail and Hold stop the current scope unless a route says otherwise. Failure never advances silently: `next_()` on a fail route must be written explicitly.
3. **Data flows through Events.** A Stage whose input needs `DraftPullRequestPublished` must sit after a Stage that produces it. Otherwise the definition **fails when it is built** with a message that names the missing producer.
4. **Four constructs, no graph:**
   - `stage(s)`: sequence;
   - `each(selector, body)`: one item at a time, with re-selection after each item;
   - `dispatch(UnionEvent, {Variant: flow_or_route})`: exhaustive switch on a closed sum;
   - `within(opener, body)`: a workspace scope.

   Rework re-runs **the same Stage only**; there are no backward or forward jumps.
5. **Run it:**

```python
result = run(implement_spec(settings, targets), SpecWork(spec), services=services, ledger=Ledger(log_dir / "ledger"), spans=spans)
sys.exit(result.exit_code)        # completed/idle/held → 0, handed_off/failed → 1, interrupted → 130
```

## A third Workflow from existing Stages

`workflows/address_once/` addresses the trusted feedback of one pull request once. It holds no Stage logic: three Stages and the shared address fragment, spliced with `.then(flow)`, which only inlines a fragment's nodes.

```python
ADDRESS_ONCE = (
    Workflow("address_once").context(PullRequestWork)
    .stage(pull_requests.validate_origin())
    .stage(pull_requests.snapshot())
    .stage(feedback.select(trust=TRUST))               # the same trust rules as pr_babysit, no polling
    .then(address.address_feedback(message=address_subject, verify=verify))
)
```

```sh
python -m software_factory.workflows.address_once --config address_once.json --pr https://github.com/owner/repo/pull/123
```

## Layout

```text
software_factory/
├── runtime/     # the definition language and the interpreter; generic, knows no GitHub, Ticket or pull request
├── events/      # the Event vocabulary Stages share; frozen dataclasses only
├── agents/      # role agents: contract, private kit, one package per role
├── stages/      # every Stage, Selector and workspace opener; flat modules independent of each other
├── flows/       # Flow fragments composed from several Stage modules; no Stage logic
├── workflows/   # one package per Workflow: definition, policy values, settings, entry point
├── shared/      # concrete infrastructure: Git, `gh`, process execution, telemetry, the agent launcher
└── tests/       # runtime, Stage, fragment, Workflow scenario and architecture tests
```

```text
workflows → flows → stages → events → runtime → (agents | shared)
```

## What keeps it this way

- `lint-imports` (run by `python -m pytest`) enforces the layers, the independence of Stages, Workflows and role agents, the private runtime engine and agent kit, that no process starts in Stages, definitions or the runtime (the discovery scheduler excepted), that Events are pure data, that no dependency-injection container is imported and that source never imports tests.
- `tests/architecture/` derives the rest from the code, with no hand-kept lists: every definition builds and validates, Stage ids are unique, every Stage is used by a Workflow, every fragment by two, no Stage factory takes a `bool`, policy modules hold values and pure functions only, nothing uses Loop's phase-reporting API, every Event and agent message round-trips through the ledger codec and each Workflow runs from its own import closure.
