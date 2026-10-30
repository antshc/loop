"""The pacing Stages: the poll Selector that never runs out and the wait between polls through the pacer service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from software_factory.events.pull_requests import PullRequestPolled
from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Flow, Item, Workflow
from software_factory.shared.pacing import Pacer
from software_factory.stages import pacing, pull_requests
from software_factory.tests.runtime.harness import StopAfter
from software_factory.tests.stages.pr_support import SLUG
from software_factory.tests.stages.watch_support import WatchRig, watch

TARGET = RepositoryTarget(SLUG, Path("/work"), Path("/trees"))


def polled() -> PullRequestPolled:
    return PullRequestPolled(TARGET, 7, {"pr": {"state": "open", "head": {"sha": "abc", "ref": "f"}}})


def polling(interval: float = 5) -> Workflow[Any]:
    """Poll the pull request and wait `interval` after each poll, without end."""
    return watch().each(pacing.polls(), Flow("poll").stage(pull_requests.snapshot()).stage(pacing.pace(interval)))


def test_pace_waits_the_interval_through_the_pacer_service_and_passes(tmp_path: Path) -> None:
    """Given a pace Stage of 30 seconds, when it runs, then the pacer is asked to wait 30 seconds and the run completes without sleeping."""
    rig = WatchRig(tmp_path)

    result = rig.run_watch(watch(pacing.pace(30)))

    assert result.status == "completed" and rig.waits == [30]


def test_the_pacer_sleeps_through_the_injected_function() -> None:
    """Given a pacer over a recording sleep, when it waits, then the sleep receives the seconds."""
    slept: list[float] = []

    Pacer(slept.append).wait(1.5)

    assert slept == [1.5]


def test_the_poll_selector_offers_the_next_numbered_poll_after_every_snapshot_taken() -> None:
    """Given the snapshots taken so far, when the selector is asked, then it offers exactly one poll numbered after them."""
    selector = pacing.polls()

    assert selector.select(pacing.PollInput(())) == [Item(pacing.Poll(1), "poll-1", "poll-1")]
    assert selector.select(pacing.PollInput((polled(), polled()))) == [Item(pacing.Poll(3), "poll-3", "poll-3")]


def test_polling_continues_poll_after_poll_until_the_stop_signal_is_asked_for(tmp_path: Path) -> None:
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
