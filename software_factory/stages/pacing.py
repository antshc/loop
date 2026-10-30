"""The pacing Stages: the unbounded poll Selector and the wait between polls."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from software_factory.events.pull_requests import PullRequestPolled
from software_factory.runtime import Evidence, History, Item, Nothing, Pass, Selector, Stage, Verdict
from software_factory.shared.pacing import Pacer


@dataclass(frozen=True)
class Poll:
    """The loop item of one poll: its number, from 1, counted over every snapshot this work item took."""

    number: int


@dataclass(frozen=True)
class PollInput:
    polled: History[PullRequestPolled]


@dataclass(frozen=True)
class PaceInput:
    pacer: Pacer


def polls() -> Selector[PollInput, Poll]:
    """One new poll after another, never running out; the runtime checks the stop signal between polls, and how a loop over them ends is the definition's routes."""
    return Selector("polls", PollInput, Poll, _next_poll)


def pace(interval: float) -> Stage[PaceInput, None, Nothing]:
    """Waits `interval` seconds through the pacer service."""
    return (
        Stage("pace", PaceInput, produces=Nothing)
        .action(lambda i: i.pacer.wait(interval))
        .gate(_waited)
    )


def _next_poll(i: PollInput) -> Sequence[Item[Poll]]:
    number = len(i.polled) + 1
    return [Item(Poll(number), f"poll-{number}", f"poll-{number}")]


def _waited(_: PaceInput, __: None, ___: Evidence) -> Verdict[Nothing]:
    return Pass(Nothing())
