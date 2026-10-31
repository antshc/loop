"""The pacing Stages: the poll Selector that never runs out and the wait between polls through the pacer service."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from loop import Flow, Item, Ledger, Nothing, Pass, Stage, Workflow, WorkItemLog
from loop.runtime._engine.scope import Scope

from tests.runtime.harness import StopAfter
from workflows.software_factory.events.pull_requests import (
    PollStarted,
    PullRequestPolled,
)
from workflows.software_factory.events.specs import RepositoryTarget
from workflows.software_factory.infrastructure.pacing import Pacer
from workflows.software_factory.stages import pacing, pull_requests
from workflows.tests.software_factory.stages.pr_support import SLUG
from workflows.tests.software_factory.stages.watch_support import (
    WORKFLOW,
    WatchRig,
    watch,
)

TARGET = RepositoryTarget(SLUG, Path("/work"), Path("/trees"))


def polled() -> PullRequestPolled:
    return PullRequestPolled(
        TARGET, 7, {"pr": {"state": "open", "head": {"sha": "abc", "ref": "f"}}}
    )


def polling(interval: float = 5) -> Workflow[Any]:
    """Poll the pull request and wait `interval` after each poll, without end."""
    return watch().each(
        pacing.polls(),
        Flow("poll")
        .stage(pacing.start())
        .stage(pull_requests.snapshot())
        .stage(pacing.pace(interval)),
    )


@dataclass(frozen=True)
class SeenInput:
    polled: PullRequestPolled


def reading(interval: float = 5) -> Workflow[Any]:
    """Like `polling`, with a Stage that resolves the poll's snapshot."""
    seen = (
        Stage("seen", SeenInput, produces=Nothing)
        .action(lambda _: None)
        .gate(lambda *_: Pass(Nothing()))
    )
    return watch().each(
        pacing.polls(),
        Flow("poll")
        .stage(pacing.start())
        .stage(pull_requests.snapshot())
        .stage(seen)
        .stage(pacing.pace(interval)),
    )


def test_pace_waits_the_interval_through_the_pacer_service_and_passes(
    tmp_path: Path,
) -> None:
    """Given a pace Stage of 30 seconds, when it runs, then the pacer is asked to wait 30 seconds and the run completes without sleeping."""
    rig = WatchRig(tmp_path)

    result = rig.run_watch(watch(pacing.pace(30)))

    assert result.status == "completed" and rig.waits == [30]


def test_the_pacer_sleeps_through_the_injected_function() -> None:
    """Given a pacer over a recording sleep, when it waits, then the sleep receives the seconds."""
    slept: list[float] = []

    Pacer(slept.append).wait(1.5)

    assert slept == [1.5]


def test_the_poll_selector_offers_polls_numbered_above_the_highest_one_recorded() -> (
    None
):
    """Given the polls recorded so far, quiet ones missing in between, when the selector is asked, then it offers polls numbered above the highest, without end."""
    selector = pacing.polls()

    def first(started: tuple[PollStarted, ...], count: int) -> list[Item[pacing.Poll]]:
        return list(itertools.islice(selector.select(pacing.PollInput(started)), count))

    assert first((), 2) == [
        Item(pacing.Poll(1), "poll-1", "poll-1"),
        Item(pacing.Poll(2), "poll-2", "poll-2"),
    ]
    assert first((PollStarted(1), PollStarted(6)), 1) == [
        Item(pacing.Poll(7), "poll-7", "poll-7")
    ]


def test_a_resumed_poll_after_quiet_polls_is_followed_by_a_new_poll_not_the_end_of_the_loop(
    tmp_path: Path,
) -> None:
    """Given a run that wrote poll-1, then quiet polls, then was interrupted inside a poll that wrote, when the next run resumes it, then it polls again under a new id instead of completing."""
    rig = WatchRig(tmp_path)
    log = rig.ledger.item(WORKFLOW, rig.watched.id)
    log.seed(
        [
            {"kind": "run-opened", "epoch": 1},
            {
                "kind": "scope-opened",
                "path": ["poll", "poll-1"],
                "item": {
                    "type": "workflows.software_factory.stages.pacing.Poll",
                    "fields": {"number": 1},
                },
                "revision": "poll-1",
                "head": None,
            },
            {
                "kind": "pure",
                "path": ["poll", "poll-1"],
                "stage": "start-poll",
                "revision": "poll-1",
                "outcome": "pass",
                "event": {
                    "type": "workflows.software_factory.events.pull_requests.PollStarted",
                    "fields": {"number": 1},
                },
                "reason": None,
                "error": None,
            },
            {"kind": "scope-closed", "path": ["poll", "poll-1"], "status": "completed"},
            {
                "kind": "scope-opened",
                "path": ["poll", "poll-6"],
                "item": {
                    "type": "workflows.software_factory.stages.pacing.Poll",
                    "fields": {"number": 6},
                },
                "revision": "poll-6",
                "head": None,
            },
            {
                "kind": "pure",
                "path": ["poll", "poll-6"],
                "stage": "start-poll",
                "revision": "poll-6",
                "outcome": "pass",
                "event": {
                    "type": "workflows.software_factory.events.pull_requests.PollStarted",
                    "fields": {"number": 6},
                },
                "reason": None,
                "error": None,
            },
        ]
    )

    result = rig.run_watch(polling(), stop=StopAfter(3))

    assert result.status == "interrupted" and rig.waits == [5, 5, 5]


def test_hundreds_of_quiet_polls_retain_no_records_and_resolve_against_a_flat_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an unchanged pull request polled 300 times, when every poll is quiet, then the run's log tracks no closed scope, buffers no record and each resolve sees as few Events on the last poll as on the second."""
    rig = WatchRig(tmp_path)
    logs: list[WorkItemLog] = []
    item = Ledger.item

    def remembered(self: Ledger, workflow: str, name: str) -> WorkItemLog:
        log = item(self, workflow, name)
        logs.append(log)
        return log

    seen: list[int] = []
    visible = Scope.visible

    def measured(self: Scope) -> list[Any]:
        events = visible(self)
        seen.append(len(events))
        return events

    monkeypatch.setattr(Ledger, "item", remembered)
    monkeypatch.setattr(Scope, "visible", measured)

    result = rig.run_watch(reading(), stop=StopAfter(300))

    (log,) = {id(log): log for log in logs if log._scopes}.values()
    assert result.status == "interrupted" and len(rig.waits) == 300
    assert len(log._scopes) == 1 and not any(scope.pending for scope in log._scopes)
    assert max(seen) <= 3


def test_polling_continues_poll_after_poll_until_the_stop_signal_is_asked_for(
    tmp_path: Path,
) -> None:
    """Given an unbounded poll loop and a stop signal that turns on after three checks, when it runs, then the pull request is polled and waited on three times and the run is interrupted."""
    rig = WatchRig(tmp_path)
    stop_after = StopAfter(3)

    result = rig.run_watch(polling(), stop=stop_after)

    assert (result.status, result.exit_code) == ("interrupted", 130)
    assert rig.waits == [5, 5, 5]


def test_a_stop_asked_for_before_the_first_poll_polls_nothing(tmp_path: Path) -> None:
    """Given a stop signal already on, when the loop runs, then no poll and no wait happens."""
    rig = WatchRig(tmp_path)

    result = rig.run_watch(polling(), stop=StopAfter(0))

    assert result.status == "interrupted" and rig.waits == []
