"""Routes: constructors, defaults and the terminal-route namespace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loop import (
    Fail,
    Flow,
    Handoff,
    Hold,
    Item,
    Next,
    NextItem,
    Pass,
    Reroute,
    Rework,
    Routes,
    Selector,
    Stage,
    Stop,
    Workflow,
    handoff,
    next_,
    next_item,
    reroute,
    rework,
    stop,
)

from .harness import Rig, WorkCtx
from .support import Alpha, Beta, NeedsTicket, NoInput, Outcome, Routed, Ticket, effect


def test_a_stage_routes_default_to_next_on_pass_and_stop_on_fail_and_hold() -> None:
    """Given no routes, when a Stage is defined, then pass goes on, fail stops failed and hold stops held."""
    assert Routes() == Routes(Next(), Stop("failed"), Stop("held"))


def test_the_route_constructors_build_the_route_values() -> None:
    """Given the route constructors, when called, then each builds its route value."""
    escalate = effect("escalate")

    assert next_() == Next()
    assert next_item() == NextItem()
    assert reroute(Alpha) == Reroute(Alpha)
    assert handoff(escalate) == Handoff(escalate)
    assert rework(max_attempts=2, then=handoff(escalate)) == Rework(
        2, Handoff(escalate)
    )


def test_a_rework_without_a_then_stops_failed_after_the_bound() -> None:
    """Given a rework with no hand-off, when its bound is spent, then the scope stops failed."""
    assert rework(max_attempts=3).then == Stop("failed")


def test_the_terminal_routes_carry_their_status_and_reason() -> None:
    """Given the stop namespace, when each is called, then its status and optional reason are set."""
    assert (stop.completed(), stop.idle()) == (Stop("completed"), Stop("idle"))
    assert stop.held("waiting") == Stop("held", "waiting")
    assert stop.failed("broken") == Stop("failed", "broken")


# ---- executing routes ----------------------------------------------------------------------------------------


def stage(
    id: str, log: list[str], verdict: Any = None, produces: Any = Routed
) -> Stage[Any, Any, Any]:
    """A check Stage that logs its id each time it runs and then gives `verdict` (a pass by default)."""

    def run(_: NoInput) -> None:
        log.append(id)

    default = Pass({Routed: Routed("x"), Beta: Beta()}.get(produces, Alpha()))
    return (
        Stage(id, NoInput, produces)
        .action(run)
        .gate(lambda i, r, e: verdict or default)
    )


def workflow(*nodes: Stage[Any, Any, Any]) -> Workflow[WorkCtx]:
    """A Workflow of the Stages in order."""
    definition = Workflow("t").context(WorkCtx)
    for node in nodes:
        definition = definition.stage(node)
    return definition


def test_a_pass_goes_on_to_the_next_stage_by_default(tmp_path: Path) -> None:
    """Given three Stages with default routes, when all pass, then they run in order and the run completes."""
    log: list[str] = []

    result = Rig(tmp_path).run(
        workflow(stage("a", log), stage("b", log), stage("c", log))
    )

    assert (result.status, log) == ("completed", ["a", "b", "c"])


def test_a_fail_with_no_fail_route_stops_the_scope_failed_and_no_later_stage_runs(
    tmp_path: Path,
) -> None:
    """Given a failing Stage with no fail route, when it runs, then the run stops failed and the later Stage never runs."""
    log: list[str] = []

    result = Rig(tmp_path).run(
        workflow(stage("a", log), stage("b", log, Fail("broke")), stage("c", log))
    )

    assert (result.status, result.reason, log) == ("failed", "broke", ["a", "b"])


def test_a_hold_with_no_hold_route_stops_the_scope_held(tmp_path: Path) -> None:
    """Given a holding Stage with no hold route, when it runs, then the run stops held and nothing advances silently."""
    log: list[str] = []

    result = Rig(tmp_path).run(workflow(stage("a", log, Hold("wait")), stage("b", log)))

    assert (result.status, result.reason, log) == ("held", "wait", ["a"])


def test_a_stage_that_fails_twice_with_a_rework_of_two_starts_exactly_two_attempts_and_follows_the_exhausted_route(
    tmp_path: Path,
) -> None:
    """Given rework of 2 that ends in a hand-off, when the Stage fails twice, then only that Stage runs twice and the hand-off runs once."""
    log: list[str] = []
    rig = Rig(tmp_path)
    escalate = (
        Stage("escalate", NoInput, Routed)
        .effect(lambda i: log.append("escalate"), reconcile=lambda i: None)
        .gate(lambda i, r, e: Pass(Routed("x")))
    )
    flaky = stage("flaky", log, Fail("again")).on_fail(
        rework(max_attempts=2, then=handoff(escalate))
    )

    result = rig.run(workflow(stage("before", log), flaky, stage("after", log)))

    assert (result.status, log) == (
        "handed_off",
        ["before", "flaky", "flaky", "escalate"],
    )
    assert [
        event.attributes
        for event in rig.spans.tree()[0].events
        if event.name == "factory.rework"
    ] == [{"next_attempt": 2, "budget": 2}]


def test_a_stage_that_fails_then_passes_on_rework_shows_two_distinct_attempt_spans(
    tmp_path: Path,
) -> None:
    """Given rework of 3, when the first attempt fails and the second passes, then two attempt spans show fail then pass."""
    log: list[str] = []
    verdicts = [Fail("first"), Pass(Routed("ok"))]
    rig = Rig(tmp_path)
    flaky = (
        Stage("flaky", NoInput, Routed)
        .effect(lambda i: log.append("flaky"), reconcile=lambda i: None)
        .gate(lambda i, r, e: verdicts.pop(0))
        .on_fail(rework(max_attempts=3))
    )

    result = rig.run(workflow(flaky, stage("after", log)))

    attempts = rig.spans.attempts("flaky")
    assert result.status == "completed" and log == ["flaky", "flaky", "after"]
    assert [
        (
            span.attributes["factory.stage.attempt"],
            span.attributes["factory.stage.outcome"],
        )
        for span in attempts
    ] == [(1, "fail"), (2, "pass")]
    assert [span.error for span in attempts] == ["first", None]
    assert rig.spans.events("flaky", "factory.rework") == [
        {"next_attempt": 2, "budget": 3}
    ]


def test_a_hand_off_route_runs_its_effect_stage_once_and_ends_the_scope_handed_off(
    tmp_path: Path,
) -> None:
    """Given a hold routed to a hand-off, when it fires, then the effect Stage runs once and the scope ends handed off."""
    log: list[str] = []
    rig = Rig(tmp_path)
    notify = (
        Stage("notify", NoInput, Routed)
        .effect(lambda i: log.append("notify"), reconcile=lambda i: None)
        .gate(lambda i, r, e: Pass(Routed("x")))
    )

    result = rig.run(
        workflow(
            stage("a", log, Hold("needs a person")).on_hold(handoff(notify)),
            stage("b", log),
        )
    )

    assert (result.status, result.reason, log) == (
        "handed_off",
        "needs a person",
        ["a", "notify"],
    )
    assert rig.spans.events("notify", "factory.handoff") == []
    assert [
        event.attributes
        for event in rig.spans.tree()[0].events
        if event.name == "factory.handoff"
    ] == [{"stage": "notify"}]


class Tickets:
    """Three Tickets; the Selector offers those not yet worked, so every finished item changes what is selected next."""

    def __init__(self) -> None:
        self.worked: list[int] = []
        self.items = [Item(Ticket(n), str(n), "r") for n in (1, 2, 3)]

    def loop(self, body: Flow, **routes: Any) -> Workflow[WorkCtx]:
        """A Workflow looping the body over the Tickets."""
        select = Selector(
            "tickets",
            NoInput,
            Ticket,
            lambda i: [
                item for item in self.items if item.value.number not in self.worked
            ],
        )
        return Workflow("t").context(WorkCtx).each(select, body, **routes)

    def work(self) -> Stage[Any, Any, Any]:
        """A Stage that marks its loop item worked."""
        return (
            Stage("work", NeedsTicket, Alpha)
            .action(lambda i: self.worked.append(i.ticket.number))
            .gate(lambda i, r, e: Pass(Alpha()))
        )


def test_a_loop_re_selects_after_each_item_until_nothing_is_left(
    tmp_path: Path,
) -> None:
    """Given a loop whose Selector drops worked items, when each item completes, then it is selected again until none remain."""
    tickets = Tickets()

    result = Rig(tmp_path).run(tickets.loop(Flow("ticket").stage(tickets.work())))

    assert result.status == "completed" and tickets.worked == [1, 2, 3]


def test_next_item_ends_the_current_loop_item_and_the_loop_goes_on(
    tmp_path: Path,
) -> None:
    """Given a body Stage routed to next-item, when it passes, then the rest of the body is skipped for every item and the loop goes on."""
    tickets, log = Tickets(), []
    body = (
        Flow("ticket")
        .stage(tickets.work().on_pass(next_item()))
        .stage(stage("never", log))
    )

    result = Rig(tmp_path).run(tickets.loop(body))

    assert result.status == "completed" and tickets.worked == [1, 2, 3] and log == []


def test_a_failed_loop_item_follows_the_loops_fail_route_and_by_default_stops_the_run_failed(
    tmp_path: Path,
) -> None:
    """Given a body that fails its first item, when the loop has no fail route, then the run stops failed; with next it moves on."""
    log: list[str] = []
    body = Flow("ticket").stage(stage("work", log, Fail("bad item")))

    tickets = Tickets()
    stopped = Rig(tmp_path / "a").run(tickets.loop(body))
    moved_on = Rig(tmp_path / "b").run(tickets.loop(body, on_fail=next_()))

    assert (stopped.status, stopped.reason) == ("failed", "bad item")
    assert moved_on.status == "completed"


def test_a_dispatch_runs_the_case_of_the_latest_variant(tmp_path: Path) -> None:
    """Given a switch over Alpha and Beta, when Beta was produced, then only the Beta case runs."""
    log: list[str] = []
    definition = workflow(
        stage("decide", log, Pass(Beta()), produces=Outcome)
    ).dispatch(
        Outcome,
        {
            Alpha: Flow("a").stage(stage("on-alpha", log, produces=Alpha)),
            Beta: Flow("b").stage(stage("on-beta", log, produces=Beta)),
        },
    )

    result = Rig(tmp_path).run(definition)

    assert result.status == "completed" and log == ["decide", "on-beta"]


def test_a_reroute_abandons_the_case_for_a_sibling_case_without_looping(
    tmp_path: Path,
) -> None:
    """Given an Alpha case that reroutes to Beta on failure, when Alpha is produced, then the Beta case runs once."""
    log: list[str] = []
    rig = Rig(tmp_path)
    definition = workflow(
        stage("decide", log, Pass(Alpha()), produces=Outcome)
    ).dispatch(
        Outcome,
        {
            Alpha: Flow("a").stage(
                stage("on-alpha", log, Fail("not me"), produces=Alpha).on_fail(
                    reroute(Beta)
                )
            ),
            Beta: Flow("b").stage(stage("on-beta", log, produces=Beta)),
        },
    )

    result = rig.run(definition)

    assert result.status == "completed" and log == ["decide", "on-alpha", "on-beta"]
    assert [
        event.attributes
        for event in rig.spans.tree()[0].events
        if event.name == "factory.reroute"
    ] == [{"to": "Beta"}]


def test_a_dispatch_case_that_is_a_stop_ends_the_scope_with_that_status(
    tmp_path: Path,
) -> None:
    """Given a switch whose Alpha case is stop-held, when Alpha is produced, then the run is held and later Stages do not run."""
    log: list[str] = []
    definition = (
        workflow(stage("decide", log, Pass(Alpha()), produces=Outcome))
        .dispatch(Outcome, {Alpha: stop.held("waiting"), Beta: stop.completed()})
        .stage(stage("after", log))
    )

    result = Rig(tmp_path).run(definition)

    assert (result.status, result.reason, log) == ("held", "waiting", ["decide"])
