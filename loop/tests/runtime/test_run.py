"""Running a Workflow: the boundary, exit codes, input resolution, skipping, declared errors and the stop signal."""

from __future__ import annotations

from pathlib import Path

import pytest
from loop import (
    DefinitionError,
    Event,
    Fail,
    Flow,
    Hold,
    Item,
    Pass,
    RunResult,
    Selector,
    Services,
    Stage,
    Workflow,
    handoff,
    stop,
)

from .harness import Rig, StopAfter, WorkCtx
from .support import (
    Alpha,
    Beta,
    Client,
    NeedsAllRouted,
    NeedsClient,
    NeedsOptionalRouted,
    NeedsRouted,
    NoInput,
    Routed,
    Ticket,
)


def passing(id: str, event: Event | None = None) -> Stage:
    """A check Stage that passes with `event`."""
    return Stage(id, NoInput, Routed).gate(lambda i, r, e: Pass(event or Routed("x")))


def failing(id: str, reason: str = "no") -> Stage:
    """A check Stage that fails."""
    return Stage(id, NoInput, Routed).gate(lambda i, r, e: Fail(reason))


def holding(id: str, reason: str = "wait") -> Stage:
    """A check Stage that holds."""
    return Stage(id, NoInput, Routed).gate(lambda i, r, e: Hold(reason))


def flow(*stages: Stage, name: str = "t") -> Workflow[WorkCtx]:
    """A Workflow of the Stages in order."""
    workflow = Workflow(name).context(WorkCtx)
    for stage in stages:
        workflow = workflow.stage(stage)
    return workflow


def test_a_run_is_refused_before_any_stage_executes_when_a_needed_service_is_missing(
    tmp_path: Path,
) -> None:
    """Given a definition that needs a Client, when it runs without one, then it is refused naming the type and nothing ran."""
    rig = Rig(tmp_path)
    ran: list[str] = []
    needs = (
        Stage("needs-client", NeedsClient, Routed)
        .action(lambda i: ran.append("ran"))
        .gate(lambda i, r, e: Pass(Routed("x")))
    )

    with pytest.raises(DefinitionError, match="Client"):
        rig.run(flow(passing("first"), needs))

    assert (
        ran == []
        and rig.spans.tree() == ()
        and not rig.ledger.item("t", "item-1").path.exists()
    )


def test_a_run_with_the_needed_service_reaches_the_stage_that_uses_it(
    tmp_path: Path,
) -> None:
    """Given a definition that needs a Client, when it runs with one, then the Stage receives that instance."""
    rig = Rig(tmp_path)
    client = Client()
    seen: list[object] = []
    uses = (
        Stage("uses-client", NeedsClient, Routed)
        .action(lambda i: seen.append(i.client))
        .gate(lambda i, r, e: Pass(Routed("x")))
    )

    result = rig.run(flow(uses), client)

    assert result.status == "completed" and seen == [client]


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("completed", 0),
        ("idle", 0),
        ("held", 0),
        ("handed_off", 1),
        ("failed", 1),
        ("interrupted", 130),
    ],
)
def test_the_exit_code_follows_the_terminal_status(status: str, code: int) -> None:
    """Given a terminal status, when its exit code is read, then completed, idle and held are 0, handed off and failed 1, interrupted 130."""
    assert RunResult(status, None, 1).exit_code == code  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("terminal", "status"),
    [
        (stop.idle(), "idle"),
        (stop.held("w"), "held"),
        (stop.failed("f"), "failed"),
        (stop.completed(), "completed"),
    ],
)
def test_a_stop_route_ends_the_run_with_its_status_and_the_matching_exit_code(
    tmp_path: Path, terminal, status
) -> None:
    """Given a Stage routing to a stop, when the run ends, then its status and exit code are the stop's."""
    rig = Rig(tmp_path)

    result = rig.run(flow(passing("first").on_pass(terminal), passing("never")))

    assert result.status == status and "never" not in [
        span.name for span in rig.spans.find("never")
    ]
    assert result.exit_code == (1 if status == "failed" else 0)


def test_a_run_ends_handed_off_with_exit_code_one_after_running_the_hand_off_stage_once(
    tmp_path: Path,
) -> None:
    """Given a failing Stage routed to a hand-off, when it runs, then the effect Stage runs once and the run ends handed off."""
    rig = Rig(tmp_path)
    labelled: list[str] = []
    escalate = (
        Stage("escalate", NoInput, Routed)
        .effect(lambda i: labelled.append("hitl"), reconcile=lambda i: None)
        .gate(lambda i, r, e: Pass(Routed("x")))
    )

    result = rig.run(
        flow(failing("work", "stuck").on_fail(handoff(escalate)), passing("never"))
    )

    assert (result.status, result.reason, result.exit_code, labelled) == (
        "handed_off",
        "stuck",
        1,
        ["hitl"],
    )


def test_a_stage_with_a_false_condition_produces_nothing_and_a_later_optional_reader_receives_none(
    tmp_path: Path,
) -> None:
    """Given a skipped producer, when a later Stage reads its Event as optional, then it receives None and a skip is recorded."""
    rig = Rig(tmp_path)
    received: list[Routed | None] = []
    reader = (
        Stage("reader", NeedsOptionalRouted, Alpha)
        .action(lambda i: received.append(i.routed))
        .gate(lambda i, r, e: Pass(Alpha()))
    )
    producer = passing("producer").when(lambda i: False)

    result = rig.run(flow(producer, reader))

    assert result.status == "completed" and received == [None]
    run_span = rig.spans.tree()[0]
    assert [
        event.attributes for event in run_span.events if event.name == "factory.skipped"
    ] == [{"stage": "producer"}]


def test_a_stage_reads_the_latest_event_and_all_events_of_the_run(
    tmp_path: Path,
) -> None:
    """Given two Stages producing the same Event under different ids, when readers read latest and all, then they see them in order."""
    rig = Rig(tmp_path)
    latest: list[Routed] = []
    everything: list[tuple[Routed, ...]] = []
    first = passing("first", Routed("one")).named("first")
    second = passing("second", Routed("two"))
    reads_latest = (
        Stage("reads-latest", NeedsRouted, Alpha)
        .reads(Routed, from_="second")
        .action(lambda i: latest.append(i.routed))
        .gate(lambda i, r, e: Pass(Alpha()))
    )
    reads_all = (
        Stage("reads-all", NeedsAllRouted, Beta)
        .action(lambda i: everything.append(i.routed))
        .gate(lambda i, r, e: Pass(Beta()))
    )

    rig.run(flow(first, second, reads_latest, reads_all))

    assert latest == [Routed("two")] and everything == [(Routed("one"), Routed("two"))]


def test_only_a_declared_exception_type_becomes_a_route_and_anything_else_propagates(
    tmp_path: Path,
) -> None:
    """Given a Workflow declaring one error type, when a Stage raises it the run stops by its route, and another type propagates."""
    rig = Rig(tmp_path)

    def raising(error: Exception) -> Stage:
        def run(_: NoInput) -> None:
            raise error

        return (
            Stage("raises", NoInput, Routed)
            .action(run)
            .gate(lambda i, r, e: Pass(Routed("x")))
        )

    declared = flow(raising(KeyError("k"))).on_error((KeyError,), stop.held("known"))
    result = rig.run(declared)
    assert (result.status, result.reason) == ("held", "known")

    with pytest.raises(ValueError, match="odd"):
        rig.run(
            flow(raising(ValueError("odd"))).on_error((KeyError,), stop.held("known"))
        )


def test_a_stop_signal_between_loop_items_ends_the_run_interrupted_with_exit_code_130(
    tmp_path: Path,
) -> None:
    """Given a loop of three items, when the stop signal is observed after the first item, then the run is interrupted."""
    rig = Rig(tmp_path)
    done: list[int] = []
    items = [Item(Ticket(n), str(n), "r") for n in (1, 2, 3)]
    select = Selector(
        "tickets",
        NoInput,
        Ticket,
        lambda i: [item for item in items if item.value.number not in done],
    )

    work = (
        Stage("work", NoInput, Alpha)
        .action(lambda i: done.append(len(done) + 1))
        .gate(lambda i, r, e: Pass(Alpha()))
    )
    workflow = Workflow("t").context(WorkCtx).each(select, Flow("ticket").stage(work))
    result = rig.run(workflow, stop=StopAfter(1))

    assert (result.status, result.exit_code, done) == ("interrupted", 130, [1])


def test_services_are_keyed_by_exact_type_and_a_copy_can_replace_one() -> None:
    """Given Services holding a Client, when it is replaced in a copy, then the copy holds the new one and the original is unchanged."""
    original, other = Client(), Client()
    services = Services(original, "text")

    replaced = services.replace(other)

    assert (
        services.get(Client) is original
        and replaced.get(Client) is other
        and replaced.get(str) == "text"
    )
    assert services.types() == {Client, str}


def test_services_can_serve_a_subclass_as_the_service_it_replaces_and_keep_it_through_a_replacement() -> (
    None
):
    """Given a fake that subclasses a service, when it is bound under the service's type, then it is served as that type and survives replacing another service."""

    class FakeClient(Client):
        pass

    fake = FakeClient()
    services = Services("text").bind(Client, fake)

    replaced = services.replace("other")

    assert (
        services.get(Client) is fake
        and replaced.get(Client) is fake
        and replaced.get(str) == "other"
    )
    assert replaced.types() == {Client, str}


def test_services_refuse_two_instances_of_one_type_and_name_a_missing_type() -> None:
    """Given Services, when two instances share a type or an absent type is read, then the error names the type."""
    with pytest.raises(ValueError, match="Client"):
        Services(Client(), Client())
    with pytest.raises(LookupError, match="Client"):
        Services().get(Client)


def test_a_declared_exception_in_an_executing_attempt_fails_that_attempt_and_takes_the_error_route(
    tmp_path: Path,
) -> None:
    """Given a declared error type, when an effect raises it, then the attempt is recorded failed and the run stops by the route."""
    rig = Rig(tmp_path)

    def down(_: NoInput) -> None:
        raise KeyError("api down")

    publish = (
        Stage("publish", NoInput, Routed)
        .effect(down, reconcile=lambda i: None)
        .gate(lambda i, r, e: Pass(Routed("x")))
    )

    result = rig.run(flow(publish).on_error((KeyError,), stop.held("api down")))

    (attempt,) = rig.spans.attempts("publish")
    assert (result.status, result.reason) == ("held", "api down")
    assert attempt.attributes["factory.stage.outcome"] == "fail" and "KeyError" in (
        attempt.error or ""
    )
    assert [r["status"] for r in rig.records("t") if r["kind"] == "attempt"] == [
        "running",
        "failed",
    ]


def test_a_stage_that_produces_an_undeclared_event_is_rejected(tmp_path: Path) -> None:
    """Given a Stage declaring Routed, when its gate passes an Alpha, then the run raises naming the Stage."""
    wrong = Stage("wrong", NoInput, Routed).gate(lambda i, r, e: Pass(Alpha()))

    with pytest.raises(DefinitionError, match="Stage 'wrong'"):
        Rig(tmp_path).run(flow(wrong))
