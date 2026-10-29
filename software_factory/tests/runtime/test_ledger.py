"""The Execution ledger: what a run writes, in which order, where, and what is read back."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from software_factory.runtime import (
    Event, Fail, Flow, History, Ledger, Pass, Stage, Workflow, rework,
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
