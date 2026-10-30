"""Once-per-key replay, at-most-N limits across runs, and the run attributes a hand-off records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from software_factory.runtime import Fail, Pass, Stage, Workflow, handoff

from .harness import Rig, WorkCtx
from .support import NeedsRouted, NoInput, Routed


class Boom(Exception):
    """A crash nothing in the runtime catches."""


def effect(calls: list[str], *, key: str = "k", outcome: Any = None, crash: bool = False, id: str = "push") -> Stage[Any, Any, Any]:
    """An effect Stage that logs each execution, then passes `Routed(<number of the call>)` unless told to fail or crash."""
    def run(_: NoInput) -> str:
        calls.append(key)
        if crash:
            raise Boom("died")
        return f"call-{len(calls)}"

    judge = (lambda i, r, e: outcome) if outcome is not None else (lambda i, r, e: Pass(Routed(r)))
    return Stage(id, NoInput, Routed).effect(run, reconcile=lambda _: None).gate(judge)


def flow(*stages: Stage[Any, Any, Any]) -> Workflow[WorkCtx]:
    """A Workflow of the Stages in order."""
    workflow = Workflow("t").context(WorkCtx)
    for stage in stages:
        workflow = workflow.stage(stage)
    return workflow


def test_a_once_per_key_stage_passes_once_and_replays_in_a_later_run(tmp_path: Path) -> None:
    """Given a once-per-key Stage, when two runs place it for the same key, then it executes once and the second run records a replay."""
    rig, calls = Rig(tmp_path), []
    definition = flow(effect(calls).once(per=lambda _: "head-1"))

    first, second = rig.run(definition), rig.run(definition)

    assert (first.status, second.status, calls) == ("completed", "completed", ["k"])
    replays = [event.attributes for event in rig.spans.roots[1].events if event.name == "factory.replay"]
    assert replays == [{"stage": "push", "outcome": "pass"}]


def test_a_replayed_once_stage_gives_later_stages_the_recorded_event(tmp_path: Path) -> None:
    """Given a once-per-key Stage that passed in an earlier run, when a later run places it, then the Stage after it reads the first run's Event."""
    rig, calls, seen = Rig(tmp_path), [], []
    reader = Stage("reader", NeedsRouted, Routed).action(lambda i: seen.append(i.routed.target)).gate(lambda i, r, e: Pass(Routed("x")))
    definition = flow(effect(calls).once(per=lambda _: "head-1"), reader)

    rig.run(definition)
    rig.run(definition)

    assert seen == ["call-1", "call-1"] and calls == ["k"]


def test_a_once_per_key_stage_runs_again_for_a_new_key(tmp_path: Path) -> None:
    """Given a once-per-key Stage, when the key changes between runs, then it executes again."""
    rig, calls = Rig(tmp_path), []
    keys = iter(["head-1", "head-2"])

    rig.run(flow(effect(calls).once(per=lambda _: next(keys))))
    rig.run(flow(effect(calls).once(per=lambda _: next(keys))))

    assert calls == ["k", "k"]


def test_a_once_per_key_stage_that_failed_is_retried_in_a_later_run(tmp_path: Path) -> None:
    """Given a once-per-key Stage whose attempt failed, when a later run places it, then it executes again and its pass is then final."""
    rig, calls = Rig(tmp_path), []

    failed = rig.run(flow(effect(calls, outcome=Fail("rejected")).once(per=lambda _: "k")))
    passed = rig.run(flow(effect(calls).once(per=lambda _: "k")))
    replayed = rig.run(flow(effect(calls).once(per=lambda _: "k")))

    assert (failed.status, passed.status, replayed.status, len(calls)) == ("failed", "completed", "completed", 2)


def test_an_at_most_n_stage_fails_without_executing_once_n_attempts_have_started(tmp_path: Path) -> None:
    """Given a Stage limited to two attempts per key, when a third run places it for that key, then it fails and the effect is not executed."""
    rig, calls = Rig(tmp_path), []
    definition = flow(effect(calls).limit(2, per=lambda _: "head-1"))

    results = [rig.run(definition) for _ in range(3)]

    assert [result.status for result in results] == ["completed", "completed", "failed"]
    assert calls == ["k", "k"]
    assert results[2].reason == "Stage 'push' has already started 2 of its 2 attempts for 'head-1'"


def test_an_at_most_n_stage_counts_an_attempt_that_was_interrupted_before_it_finished(tmp_path: Path) -> None:
    """Given a limited Stage whose effect crashed the run, when later runs place it, then the crashed attempt still counts against the cap."""
    rig, calls = Rig(tmp_path), []

    with pytest.raises(Boom):
        rig.run(flow(effect(calls, crash=True).limit(2, per=lambda _: "head-1")))
    ok = rig.run(flow(effect(calls).limit(2, per=lambda _: "head-1")))
    over = rig.run(flow(effect(calls).limit(2, per=lambda _: "head-1")))

    assert (ok.status, over.status, len(calls)) == ("completed", "failed", 2)


def test_an_at_most_n_stage_counts_each_key_on_its_own(tmp_path: Path) -> None:
    """Given a Stage limited to one attempt per key, when two keys are placed, then each gets its one attempt."""
    rig, calls = Rig(tmp_path), []
    keys = iter(["head-1", "head-2", "head-1"])

    results = [rig.run(flow(effect(calls).limit(1, per=lambda _, keys=keys: next(keys)))) for _ in range(3)]

    assert [result.status for result in results] == ["completed", "completed", "failed"]


def test_a_failed_limit_takes_the_stage_fail_route(tmp_path: Path) -> None:
    """Given an exhausted limit and a hand-off fail route, when the Stage is placed, then the run is handed off with the limit's reason."""
    rig, calls, handed = Rig(tmp_path), [], []
    block = Stage("block", NoInput, Routed).effect(lambda _: handed.append("blocked"), reconcile=lambda _: None).gate(lambda i, r, e: Pass(Routed("b")))
    definition = flow(effect(calls).limit(1, per=lambda _: "k").on_fail(handoff(block)))

    rig.run(definition)
    result = rig.run(definition)

    assert (result.status, handed, len(calls)) == ("handed_off", ["blocked"], 1)
    assert result.reason == "Stage 'push' has already started 1 of its 1 attempts for 'k'"


def test_a_hand_off_records_the_attributes_it_derives_on_the_closed_run_and_the_run_span(tmp_path: Path) -> None:
    """Given a hand-off Stage that records run attributes, when the run hands off, then the ledger and the run span carry them with the route's reason."""
    rig = Rig(tmp_path)
    block = (
        Stage("block", NoInput, Routed).effect(lambda _: "fp-1", reconcile=lambda _: None).gate(lambda i, r, e: Pass(Routed(r)))
        .records_run(lambda event, reason: {"fingerprint": event.target, "reason": reason or ""})
    )
    definition = flow(Stage("check", NoInput, Routed).gate(lambda i, r, e: Fail("CI is red")).on_fail(handoff(block)))

    result = rig.run(definition)

    closed = rig.records("t")[-1]
    assert result.status == "handed_off" and closed["kind"] == "run-closed"
    assert closed["attributes"] == {"fingerprint": "fp-1", "reason": "CI is red"}
    assert rig.spans.roots[0].attributes["fingerprint"] == "fp-1"


def test_a_run_that_does_not_hand_off_records_no_attributes(tmp_path: Path) -> None:
    """Given a run that completes, when it closes, then its closed record carries no attributes."""
    rig = Rig(tmp_path)

    rig.run(flow(Stage("check", NoInput, Routed).gate(lambda i, r, e: Pass(Routed("x")))))

    assert rig.records("t")[-1]["attributes"] == {}
