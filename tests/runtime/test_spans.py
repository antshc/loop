"""The span tree a run reports: run, scope and Stage attempt spans, their events, lazy scopes and the console log."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from loop import (
    Fail, Flow, HarnessCommits, Hold, Item, LogSpans, Pass, RecordedSpan, Selector, Stage, Workflow, handoff, rework,
)

from .harness import TESTER, Ask, Rig, WorkCtx, editing
from .support import Alpha, Beta, NeedsTicket, NoInput, Outcome, Routed, Ticket


def shape(span: RecordedSpan) -> tuple[str, list[Any]]:
    """A span as its name and its children's shapes."""
    return span.name, [shape(child) for child in span.children]


def worked_tickets(rig: Rig, body: Flow, *numbers: int) -> Workflow[WorkCtx]:
    """Tickets worked one after another inside the repository's workspace; a Stage named `mark` ends each item."""
    done: list[int] = []
    items = [Item(Ticket(n), f"#{n}", "r") for n in numbers]
    select = Selector("tickets", NoInput, Ticket, lambda i: [item for item in items if item.value.number not in done])
    mark = Stage("mark", NeedsTicket, Alpha).action(lambda i: done.append(i.ticket.number)).gate(lambda i, r, e: Pass(Alpha()))
    return Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("repo").each(select, body.stage(mark)))


def implement(contract: Any = None, gate: Any = None) -> Stage[Any, Any, Any]:
    """An agent Stage that edits files; the runtime commits them by default."""
    contract = contract or HarnessCommits(message=lambda i, event: "implemented")
    stage = Stage("work", NoInput, Routed).agent(TESTER, lambda i, path: Ask("go")).contract(contract)
    return stage.gate(gate or (lambda i, reply, evidence: Pass(Routed("done"))))


def test_the_spans_form_a_tree_of_run_then_scopes_then_stage_attempts(tmp_path: Path) -> None:
    """Given tickets worked in a workspace, when the run is recorded, then each attempt span sits under its item scope under the run."""
    rig = Rig(tmp_path).agent_does(editing("a.txt"), editing("b.txt"))

    result = rig.run(worked_tickets(rig, Flow("ticket").stage(implement()), 1, 2))

    assert result.status == "completed"
    assert [shape(span) for span in rig.spans.tree()] == [
        ("t", [("checkout", [("ticket #1", [("work", [])]), ("ticket #2", [("work", [])])])])
    ]


def test_each_span_carries_its_role_and_the_factory_attributes(tmp_path: Path) -> None:
    """Given one worked ticket, when the run is recorded, then run, scope and attempt carry their roles and attributes."""
    rig = Rig(tmp_path).agent_does(editing())

    rig.run(worked_tickets(rig, Flow("ticket").stage(implement()), 1))

    run_span = rig.spans.tree()[0]
    scope = rig.spans.find("ticket #1")[0]
    (attempt,) = rig.spans.attempts("work")
    assert (run_span.role, scope.role, attempt.role) == ("engineer", "code", "tester")
    assert run_span.attributes == {
        "factory.workflow": "t", "factory.work_item": "item-1", "factory.epoch": 1, "factory.run.status": "completed",
    }
    assert scope.attributes == {
        "factory.scope": "ticket", "factory.item": "#1", "factory.revision": "r", "factory.scope.status": "completed",
    }
    assert {key: attempt.attributes[key] for key in (
        "factory.stage.id", "factory.stage.attempt", "factory.stage.executor", "factory.stage.contract", "factory.stage.outcome",
    )} == {
        "factory.stage.id": "work", "factory.stage.attempt": 1, "factory.stage.executor": "agent",
        "factory.stage.contract": "HarnessCommits", "factory.stage.outcome": "pass",
    }
    assert attempt.attributes["factory.key.item"] == "t/item-1/checkout/ticket/#1/work"
    assert attempt.attributes["factory.key.epoch"] == 1


def test_gate_contract_verify_and_commit_are_span_events_on_the_attempt(tmp_path: Path) -> None:
    """Given a runtime-commit attempt that passes, when it is recorded, then gate, contract, verify and commit are its events in order."""
    rig = Rig(tmp_path).agent_does(editing())
    stage = implement(HarnessCommits(message=lambda i, event: "implemented", verify=lambda path: None))

    rig.run(worked_tickets(rig, Flow("ticket").stage(stage), 1))

    (attempt,) = rig.spans.attempts("work")
    assert [event.name for event in attempt.events] == ["factory.gate", "factory.contract", "factory.verify", "factory.commit"]
    assert attempt.events[0].attributes == {"verdict": "pass"}


def test_restore_and_hand_off_are_span_events(tmp_path: Path) -> None:
    """Given a failing attempt that restores and hands off, when it is recorded, then restore and hand-off are events of the attempt."""
    rig = Rig(tmp_path).agent_does(editing())
    notify = Stage("notify", NoInput, Routed).effect(lambda i: None, reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed("x")))
    stage = implement(gate=lambda i, r, e: Fail("no")).on_fail(handoff(notify))

    result = rig.run(worked_tickets(rig, Flow("ticket").stage(stage), 1))

    (attempt,) = rig.spans.attempts("work")
    assert result.status == "failed"
    assert [event.name for event in attempt.events] == ["factory.gate", "factory.restore", "factory.handoff"]


def test_a_held_attempt_is_not_an_error_and_a_failed_one_is(tmp_path: Path) -> None:
    """Given a held run and a failed run, when recorded, then the hold shows outcome hold with no error and the failure shows an error."""
    held, failed = Rig(tmp_path / "a").agent_does(editing()), Rig(tmp_path / "b").agent_does(editing())

    held.run(worked_tickets(held, Flow("ticket").stage(implement(gate=lambda i, r, e: Hold("wait"))), 1))
    failed.run(worked_tickets(failed, Flow("ticket").stage(implement(gate=lambda i, r, e: Fail("no"))), 1))

    (held_attempt,) = held.spans.attempts("work")
    (failed_attempt,) = failed.spans.attempts("work")
    assert (held_attempt.attributes["factory.stage.outcome"], held_attempt.error) == ("hold", None)
    assert (failed_attempt.attributes["factory.stage.outcome"], failed_attempt.error) == ("fail", "no")
    assert held.spans.find("ticket #1")[0].error is None and failed.spans.find("ticket #1")[0].error == "no"
    assert held.spans.tree()[0].error is None and failed.spans.tree()[0].error == "no"


def test_a_fail_then_pass_rework_shows_as_two_attempt_spans_with_their_outcomes(tmp_path: Path) -> None:
    """Given a rework that fails once then passes, when recorded, then two attempt spans show fail then pass and a rework event."""
    rig = Rig(tmp_path).agent_does(editing())
    verdicts = [Fail("tests red"), Pass(Routed("done"))]
    stage = implement(gate=lambda i, r, e: verdicts.pop(0)).on_fail(rework(max_attempts=2))

    rig.run(worked_tickets(rig, Flow("ticket").stage(stage), 1))

    attempts = rig.spans.attempts("work")
    assert [(a.attributes["factory.stage.attempt"], a.attributes["factory.stage.outcome"]) for a in attempts] == [(1, "fail"), (2, "pass")]
    assert [event.name for event in attempts[0].events][-1] == "factory.rework"
    assert rig.spans.events("work", "factory.rework") == [{"next_attempt": 2, "budget": 2}]


def test_a_scope_with_only_quiet_pure_passes_exports_no_span_and_writes_no_ledger_record(tmp_path: Path) -> None:
    """Given a loop whose body only passes a pure check, when it runs, then no scope span is exported and no scope is recorded."""
    rig = Rig(tmp_path)
    check = Stage("check", NeedsTicket, Alpha).gate(lambda i, r, e: Pass(Alpha()))

    result = rig.run(worked_tickets(rig, Flow("ticket").stage(check), 1, 2))

    assert result.status == "completed"
    assert [shape(span) for span in rig.spans.tree()] == [("t", [])] and rig.spans.tree()[0].events == []
    assert [record["kind"] for record in rig.records("t")] == ["run-opened", "run-closed"]


def test_a_dispatch_in_a_scope_that_does_nothing_else_exports_no_span_and_writes_no_record(tmp_path: Path) -> None:
    """Given a loop whose body only decides and switches to a case of pure checks, when it runs, then no scope span, no dispatch event and no scope record is exported."""
    rig = Rig(tmp_path)
    decide = Stage("decide", NeedsTicket, Outcome).gate(lambda i, r, e: Pass(Alpha()))
    on_alpha = Stage("on-alpha", NeedsTicket, Alpha).gate(lambda i, r, e: Pass(Alpha()))
    on_beta = Stage("on-beta", NeedsTicket, Beta).gate(lambda i, r, e: Pass(Beta()))
    body = Flow("ticket").stage(decide).dispatch(Outcome, {Alpha: Flow("a").stage(on_alpha), Beta: Flow("b").stage(on_beta)})

    result = rig.run(worked_tickets(rig, body, 1, 2))

    assert result.status == "completed"
    assert [shape(span) for span in rig.spans.tree()] == [("t", [])] and rig.spans.tree()[0].events == []
    assert [record["kind"] for record in rig.records("t")] == ["run-opened", "run-closed"]


def test_a_once_per_key_replay_in_a_scope_that_does_nothing_else_exports_no_span_and_writes_no_record(tmp_path: Path) -> None:
    """Given a once-per-key Stage that passed in an earlier run, when a later run replays it in a loop item, then that run exports no scope span and writes neither a scope nor a replay record."""
    rig = Rig(tmp_path)
    once = Stage("push", NoInput, Routed).effect(lambda i: "x", reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed(r))).once(per=lambda _: "k")
    definition = worked_tickets(rig, Flow("ticket").stage(once), 1)

    rig.run(definition)
    first = [record["kind"] for record in rig.records("t")]
    rig.run(worked_tickets(rig, Flow("ticket").stage(once), 1))

    assert [shape(span) for span in rig.spans.roots[1:]] == [("t", [])]
    assert [record["kind"] for record in rig.records("t")][len(first):] == ["run-opened", "run-closed"]


def test_a_quiet_pure_pass_is_reported_once_its_scope_opens_for_another_reason(tmp_path: Path) -> None:
    """Given a pure check before an attempt, when the scope opens for the attempt, then the check's gate shows as a scope event."""
    rig = Rig(tmp_path).agent_does(editing())
    check = Stage("check", NeedsTicket, Alpha).gate(lambda i, r, e: Pass(Alpha()))

    rig.run(worked_tickets(rig, Flow("ticket").stage(check).stage(implement()), 1))

    scope = rig.spans.find("ticket #1")[0]
    assert [(event.name, event.attributes["stage"]) for event in scope.events][0] == ("factory.gate", "check")


def test_a_check_that_does_not_pass_is_reported_on_its_scope(tmp_path: Path) -> None:
    """Given a pure check that fails, when it runs, then the scope opens, shows the gate event and the span is an error."""
    rig = Rig(tmp_path)
    check = Stage("check", NeedsTicket, Alpha).gate(lambda i, r, e: Fail("not ready"))

    result = rig.run(worked_tickets(rig, Flow("ticket").stage(check), 1))

    scope = rig.spans.find("ticket #1")[0]
    assert result.status == "failed" and scope.error == "not ready"
    assert [(event.name, event.attributes["verdict"]) for event in scope.events] == [("factory.gate", "fail")]
    assert rig.spans.attempts("check") == []


def test_the_console_logs_exactly_one_line_per_finished_span(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Given a run with an attempt that fails, when LogSpans reports it, then one INFO line is logged per finished span."""
    rig = Rig(tmp_path)
    rig.spans = LogSpans()
    work = Stage("work", NoInput, Routed).effect(lambda i: None, reconcile=lambda i: None).gate(lambda i, r, e: Fail("no"))
    caplog.set_level(logging.INFO, logger="loop.runtime")

    rig.run(Workflow("t").context(WorkCtx).stage(work))

    assert [record.getMessage() for record in caplog.records] == ["work attempt 1 · fail · no", "t · failed · no"]
    assert {record.levelno for record in caplog.records} == {logging.INFO}
