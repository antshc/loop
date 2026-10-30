"""The Execution ledger: what a run writes, in which order, where, and what is read back."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from software_factory.runtime import (
    Event, Fail, Flow, History, Ledger, Pass, Stage, WorkItemLocked, Workflow, handoff, rework,
)
from software_factory.runtime._engine.codec import decode, encode

from .harness import TESTER, Ask, Rig, WorkCtx, editing
from .support import Alpha, NoInput, Routed


class Color(StrEnum):
    RED = "red"


@dataclass(frozen=True)
class Found(Event):
    numbers: tuple[int, ...]
    where: Path | None
    color: Color
    inner: Routed
    extras: dict[str, int]


@dataclass(frozen=True)
class NeedsFoundHistory:
    past: History[Found]


def found() -> Found:
    return Found((1, 2), Path("a/b"), Color.RED, Routed("x"), {"k": 3})


def test_a_stored_dataclass_decodes_back_to_an_equal_value() -> None:
    """Given a nested Event with tuples, paths, enums, mappings and nested Events, when encoded and decoded, then it is equal."""
    stored = json.loads(json.dumps(encode(found())))

    assert decode(stored, Found) == found()


def test_each_work_item_has_its_own_versioned_log_under_its_workflow(tmp_path: Path) -> None:
    """Given two work items whose names sanitize alike, when each is logged, then they never share a directory."""
    ledger = Ledger(tmp_path)

    first, second = ledger.item("flow", "ticket/12"), ledger.item("flow", "ticket-12")

    assert first.path != second.path and first.path.parent.parent == tmp_path / "flow"
    assert first.path.name == "log.jsonl" and first.path.parent.name.startswith("ticket-12-")


def effect_stage(id: str, run: Any, event: Event = Routed("x")) -> Stage[Any, Any, Any]:
    return Stage(id, NoInput, type(event)).effect(run, reconcile=lambda i: None).gate(lambda i, r, e: Pass(event))


def test_an_attempts_intent_is_in_the_ledger_before_its_executor_runs(tmp_path: Path) -> None:
    """Given an effect Stage, when its executor reads the ledger, then the running attempt is already recorded."""
    rig = Rig(tmp_path)
    seen: list[dict[str, Any]] = []

    def effect(_: NoInput) -> None:
        seen.extend(record for record in rig.records("t") if record["kind"] == "attempt")

    rig.run(Workflow("t").context(WorkCtx).stage(effect_stage("publish", effect)))

    assert [(record["status"], record["n"]) for record in seen] == [("running", 1)]
    assert seen[0]["key"] == {"item": "t/item-1/publish", "revision": "r1", "epoch": 1}


def test_a_run_writes_its_records_in_order_with_the_version_and_the_scope_head(tmp_path: Path) -> None:
    """Given a passing agent attempt in a workspace, when the run ends, then the ledger holds the open, attempt and close records in order."""
    rig = Rig(tmp_path).agent_does(editing())
    start = rig.repo.head()
    stage = (
        Stage("work", NoInput, Routed).agent(TESTER, lambda i, path: Ask("go")).gate(lambda i, r, e: Pass(Routed("done")))
    )

    rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("repo").stage(stage)))

    records = rig.records("t")
    assert [(r["kind"], r.get("status")) for r in records] == [
        ("run-opened", None), ("scope-opened", None), ("attempt", "running"), ("attempt", "executed"),
        ("attempt", "passed"), ("scope-closed", "completed"), ("run-closed", "completed"),
    ]
    assert {record["v"] for record in records} == {1}
    assert records[1]["head"] == start and records[1]["path"] == ["checkout"]
    assert records[4]["event"] == {"type": f"{Routed.__module__}.Routed", "fields": {"target": "done"}}


def test_the_epoch_advances_after_a_finished_run_and_repeats_after_an_unfinished_one(tmp_path: Path) -> None:
    """Given a finished run and then a crashed one, when the next runs start, then the epoch advances once and then stays."""
    rig = Rig(tmp_path)
    ok = Workflow("t").context(WorkCtx).stage(effect_stage("publish", lambda i: None))

    def crash(_: NoInput) -> None:
        raise RuntimeError("down")

    crashing = Workflow("t").context(WorkCtx).stage(effect_stage("publish", crash))
    epochs = [rig.run(ok).epoch, rig.run(ok).epoch]
    with pytest.raises(RuntimeError, match="down"):
        rig.run(crashing)
    epochs.append(rig.run(ok).epoch)

    assert epochs == [1, 2, 3]
    assert [r["epoch"] for r in rig.records("t") if r["kind"] == "run-opened"] == [1, 2, 3, 3]


def test_a_torn_last_line_is_ignored_as_if_it_was_never_written(tmp_path: Path) -> None:
    """Given a log whose last line was cut off, when the next run starts, then the run reads the rest and goes on."""
    rig = Rig(tmp_path)
    ok = Workflow("t").context(WorkCtx).stage(effect_stage("publish", lambda i: None))
    rig.run(ok)
    log = rig.ledger.item("t", "item-1").path
    log.write_text(log.read_text() + '{"v":1,"kind":"run-op')

    assert rig.run(ok).epoch == 2


def test_history_reads_the_events_of_earlier_runs_of_the_work_item(tmp_path: Path) -> None:
    """Given a Workflow that reads History before it produces an Event, when it runs twice, then only the second run receives the first run's Event."""
    rig = Rig(tmp_path)
    seen: list[tuple[Found, ...]] = []
    reader = Stage("read", NeedsFoundHistory, Alpha).action(lambda i: seen.append(i.past)).gate(lambda i, r, e: Pass(Alpha()))
    workflow = Workflow("t").context(WorkCtx).stage(reader).stage(effect_stage("find", lambda i: None, found()))

    rig.run(workflow)
    rig.run(workflow)

    assert seen == [(), (found(),)]


def test_every_rework_attempt_is_a_started_attempt_in_the_ledger(tmp_path: Path) -> None:
    """Given a Stage that fails twice under a rework of two, when it runs, then two attempts start under one key."""
    rig = Rig(tmp_path)
    flaky = (
        Stage("flaky", NoInput, Routed).effect(lambda i: None, reconcile=lambda i: None)
        .gate(lambda i, r, e: Fail("again"))
        .on_fail(rework(max_attempts=2))
    )

    rig.run(Workflow("t").context(WorkCtx).stage(flaky))

    attempts = [(r["n"], r["status"]) for r in rig.records("t") if r["kind"] == "attempt"]
    assert attempts == [(1, "running"), (1, "executed"), (1, "failed"), (2, "running"), (2, "executed"), (2, "failed")]


def test_a_second_process_on_a_locked_work_item_exits_handed_off_without_writing(tmp_path: Path) -> None:
    """Given a work item whose lock another process holds, when a run starts, then it exits handed off, runs nothing and writes no record."""
    rig = Rig(tmp_path)
    ran: list[str] = []
    publish = effect_stage("publish", lambda i: ran.append("x"))
    log = rig.ledger.item("t", "item-1")

    with log.lock():
        with pytest.raises(WorkItemLocked):
            with rig.ledger.item("t", "item-1").lock():
                pass
        result = rig.run(Workflow("t").context(WorkCtx).stage(publish))

    assert (result.status, result.exit_code, ran) == ("handed_off", 1, [])
    assert not log.path.exists() and "locked" in (result.reason or "")


def test_the_lock_is_free_again_once_a_run_ends(tmp_path: Path) -> None:
    """Given a finished run, when another run starts, then it takes the lock and completes."""
    rig = Rig(tmp_path)
    ok = Workflow("t").context(WorkCtx).stage(effect_stage("publish", lambda i: None))

    assert [rig.run(ok).status, rig.run(ok).status] == ["completed", "completed"]


def test_work_items_that_differ_only_in_punctuation_keep_separate_progress(tmp_path: Path) -> None:
    """Given two work items whose ids differ only in punctuation, when each runs, then each has its own epoch counter and log."""
    rig = Rig(tmp_path)
    ok = Workflow("t").context(WorkCtx).stage(effect_stage("publish", lambda i: None))

    epochs = [rig.run(ok, context=WorkCtx(id=item)).epoch for item in ("ticket/12", "ticket-12", "ticket/12")]

    assert epochs == [1, 1, 2]
    assert rig.records("t", "ticket/12") != rig.records("t", "ticket-12")


def test_the_status_api_reports_the_latest_run_and_its_attributes(tmp_path: Path) -> None:
    """Given a run handed off with a fingerprint, when intake reads the status, then it sees the status, the reason, the attributes and whether a release followed."""
    rig = Rig(tmp_path)
    block = effect_stage("block", lambda i: "fp-9", Routed("b")).records_run(lambda event, reason: {"fingerprint": "fp-9"})
    check = Stage("check", NoInput, Routed).gate(lambda i, r, e: Fail("CI is red")).on_fail(handoff(block))
    never = rig.ledger.status("t", "item-1")

    rig.run(Workflow("t").context(WorkCtx).stage(check))
    before = rig.ledger.status("t", "item-1")
    rig.ledger.release("t", "item-1", "retry")
    after = rig.ledger.status("t", "item-1")

    assert never is None
    assert before is not None and (before.epoch, before.status, before.reason) == (1, "handed_off", "CI is red")
    assert dict(before.attributes) == {"fingerprint": "fp-9"} and before.released is False
    assert after is not None and after.released is True


def test_the_status_of_a_run_that_never_closed_is_running(tmp_path: Path) -> None:
    """Given a run that crashed, when intake reads the status, then it reads running."""
    rig = Rig(tmp_path)

    def crash(_: NoInput) -> None:
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        rig.run(Workflow("t").context(WorkCtx).stage(effect_stage("publish", crash)))

    status = rig.ledger.status("t", "item-1")

    assert status is not None and (status.epoch, status.status) == (1, "running")


def test_every_record_kind_is_versioned_json_that_reads_back(tmp_path: Path) -> None:
    """Given runs that write every record kind, when the log is read line by line, then each line is a versioned JSON object and every kind appeared."""
    rig = Rig(tmp_path)
    once = effect_stage("push", lambda i: "x").once(per=lambda _: "k")
    check = Stage("check", NoInput, Routed).gate(lambda i, r, e: Pass(Routed("ok")))
    tail = effect_stage("tail", lambda i: "y", Alpha())
    workflow = Workflow("t").context(WorkCtx).stage(check).within(rig.checkout(), Flow("repo").stage(once).stage(tail))

    rig.run(workflow)
    rig.run(workflow)
    rig.ledger.release("t", "item-1", "go")

    records = [json.loads(line) for line in rig.ledger.item("t", "item-1").path.read_text().splitlines()]
    assert {record["v"] for record in records} == {1}
    assert {record["kind"] for record in records} == {
        "run-opened", "scope-opened", "attempt", "pure", "replayed", "released", "scope-closed", "run-closed",
    }


def test_seeded_records_are_written_in_one_write_as_versioned_lines_that_read_back_in_order(tmp_path: Path) -> None:
    """Given records adopted from another source, when they are seeded, then each is a versioned line that reads back in order and the status reflects them."""
    log = Rig(tmp_path).ledger.item("t", "item-1")

    log.seed([{"kind": "run-opened", "epoch": 1}, {"kind": "run-closed", "status": "completed", "reason": None, "attributes": {}}])

    assert [record["kind"] for record in log.records()] == ["run-opened", "run-closed"]
    assert {json.loads(line)["v"] for line in log.path.read_text().splitlines()} == {1}
    status = log.status()
    assert status is not None and (status.epoch, status.status) == (1, "completed")


def test_a_record_written_after_a_torn_line_starts_on_a_line_of_its_own(tmp_path: Path) -> None:
    """Given a log ending in a fragment, when the next run appends, then the fragment is gone and every line parses."""
    rig = Rig(tmp_path)
    ok = Workflow("t").context(WorkCtx).stage(effect_stage("publish", lambda i: None))
    rig.run(ok)
    log = rig.ledger.item("t", "item-1").path
    log.write_text(log.read_text() + '{"v":1,"kind":"run-op')

    rig.run(ok)

    assert [json.loads(line)["kind"] for line in log.read_text().splitlines()][-2:] == ["attempt", "run-closed"]
    assert [r["epoch"] for r in rig.records("t") if r["kind"] == "run-opened"] == [1, 2]
