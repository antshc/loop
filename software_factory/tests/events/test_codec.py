"""Every Event the Stages produce is a frozen dataclass that the ledger codec writes and reads back unchanged."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import software_factory.events as events
from software_factory.events.pull_requests import DraftPullRequestPublished
from software_factory.events.specs import (
    CheckoutPrepared, DeliveryAnnounced, RepositoryTarget, SpecBlocked, SpecFailureCommented, SpecHandedToHuman, SpecHeld,
    SpecRouted, WorkFound,
)
from software_factory.events.tickets import Delivery, TicketClosed, TicketEscalated, TicketImplemented, TicketRejected
from software_factory.runtime import Event
from software_factory.runtime._engine.codec import decode, encode

TARGET = RepositoryTarget("owner/repo", Path("/work/repo"), Path("/work/repo.worktrees"), chore=False)

SAMPLES: list[Event] = [
    SpecRouted(TARGET, "Checkout", "main", "add-login-page"),
    SpecHeld("no target"),
    CheckoutPrepared("main"),
    WorkFound((10, 11)),
    DeliveryAnnounced("https://example.test/pull/1"),
    DeliveryAnnounced(None),
    SpecHandedToHuman("hitl", "needs a person"),
    SpecBlocked("hitl", "blocked", labelled=True),
    SpecFailureCommented("failed"),
    TicketImplemented(10, "abc1234", "summary", "verification"),
    TicketRejected(10, "declined"),
    TicketClosed(10, "abc1234"),
    TicketEscalated(10, "hitl", "declined"),
    Delivery("abc1234", ("abc1234 ccode(Checkout|10): implement",)),
    DraftPullRequestPublished("https://example.test/pull/1", "add-login-page", pushed=True),
    DraftPullRequestPublished(None, "add-login-page", pushed=False),
]


@pytest.mark.parametrize("event", SAMPLES, ids=lambda event: type(event).__name__)
def test_an_event_round_trips_through_the_ledger_codec(event: Event) -> None:
    """Given an Event, when it is encoded and decoded as its own type, then the same Event comes back."""
    assert decode(encode(event), type(event)) == event


def test_every_event_class_is_frozen_and_has_a_round_trip_sample() -> None:
    """Given every Event defined in the events package, when scanned, then each is a frozen dataclass with a sample above."""
    defined = {
        member
        for module in pkgutil.iter_modules(events.__path__)
        for _, member in inspect.getmembers(importlib.import_module(f"{events.__name__}.{module.name}"), inspect.isclass)
        if issubclass(member, Event) and member is not Event and member.__module__.startswith(events.__name__)
    }

    assert all(dataclasses.is_dataclass(event) and event.__dataclass_params__.frozen for event in defined)
    assert defined == {type(sample) for sample in SAMPLES}
