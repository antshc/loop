"""The pacing Stages: the unbounded poll Selector and the wait between polls."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass

from workflows.software_factory.events.pull_requests import PollStarted
from loop import Evidence, History, Item, Nothing, Pass, Selector, Stage, Verdict
from workflows.software_factory.infrastructure.pacing import Pacer


@dataclass(frozen=True)
class Poll:
    """The loop item of one poll: its number, from 1, above every poll this work item recorded."""

    number: int


@dataclass(frozen=True)
class PollInput:
    started: History[PollStarted]


@dataclass(frozen=True)
class StartInput:
    poll: Poll


@dataclass(frozen=True)
class PaceInput:
    pacer: Pacer


def polls() -> Selector[PollInput, Poll]:
    """One new poll after another, never running out; the runtime checks the stop signal between polls, and how a loop over them ends is the definition's routes."""
    return Selector("polls", PollInput, Poll, _next_poll)


def start() -> Stage[StartInput, PollStarted, PollStarted]:
    """Records the poll's number; the first Stage of a poll, so a poll that writes anything carries it."""
    return (
        Stage("start-poll", StartInput, produces=PollStarted)
        .action(lambda i: PollStarted(i.poll.number))
        .gate(lambda _, started, __: Pass(started))
    )


def pace(interval: float) -> Stage[PaceInput, None, Nothing]:
    """Waits `interval` seconds through the pacer service."""
    return (
        Stage("pace", PaceInput, produces=Nothing)
        .action(lambda i: i.pacer.wait(interval))
        .gate(_waited)
    )


def _next_poll(i: PollInput) -> Iterator[Item[Poll]]:
    """Every number above the highest recorded poll; a quiet poll records nothing, so the loop skips the ids it already ran in this process."""
    first = max((event.number for event in i.started), default=0) + 1
    return (Item(Poll(number), f"poll-{number}", f"poll-{number}") for number in itertools.count(first))


def _waited(_: PaceInput, __: None, ___: Evidence) -> Verdict[Nothing]:
    return Pass(Nothing())
