"""Every Event the Stages produce is a frozen dataclass that the ledger codec writes and reads back unchanged."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest
from loop import Event
from loop.runtime._engine.codec import decode, encode

import workflows.software_factory.events as events
from workflows.software_factory.events.checks import ChecksPassed
from workflows.software_factory.events.chore import ChoreDiscarded, ChoreRefined
from workflows.software_factory.events.ci import (
    BranchBroken,
    CiFixed,
    CiRerun,
    Diagnosis,
    Transient,
)
from workflows.software_factory.events.feedback import (
    FeedbackBatch,
    FeedbackGrouped,
    FeedbackHandled,
    GroupedIssue,
    IssueResolved,
    IssueUnclear,
    ReplyDraft,
)
from workflows.software_factory.events.pull_requests import (
    CiFailing,
    CopilotDue,
    CopilotRequested,
    DraftPullRequestPublished,
    Finalized,
    MergeConflict,
    MergePlanned,
    MergePrepared,
    MergePublished,
    MergeVerified,
    NextAction,
    OriginValidated,
    PollStarted,
    PrBlocked,
    PullRequestClosed,
    PullRequestPolled,
    ReadinessCheck,
    ReadinessReported,
    Unsupported,
)
from workflows.software_factory.events.review import (
    PullRequestReviewed,
    ReviewAxis,
    ReviewFailed,
    ReviewFinding,
)
from workflows.software_factory.events.specs import (
    CheckoutPrepared,
    DeliveryAnnounced,
    RepositoryTarget,
    SpecBlocked,
    SpecFailureCommented,
    SpecHandedToHuman,
    SpecHeld,
    SpecRouted,
    WorkFound,
)
from workflows.software_factory.events.tickets import (
    Delivery,
    PlannedTicket,
    PlanProposed,
    TicketClosed,
    TicketEscalated,
    TicketImplemented,
    TicketRejected,
    TicketsCreated,
)

TARGET = RepositoryTarget(
    "owner/repo", Path("/work/repo"), Path("/work/repo.worktrees"), chore=False
)

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
    TicketClosed(10, "abc1234", "Add login form"),
    TicketEscalated(10, "hitl", "declined"),
    Delivery("abc1234", ("abc1234 ccode(Checkout|10): implement",)),
    DraftPullRequestPublished(
        "https://example.test/pull/1", "add-login-page", pushed=True
    ),
    DraftPullRequestPublished(None, "add-login-page", pushed=False),
    PlanProposed(
        (
            PlannedTicket("Add form", "Build it"),
            PlannedTicket("Add tests", "Cover it", (0,)),
        )
    ),
    TicketsCreated((10, 11)),
    ChoreRefined("renamed a helper", ("old bug",), "tidy", committed=True),
    ChoreDiscarded("tests failed", ("old bug",)),
    PullRequestReviewed(
        (ReviewAxis("bugs", "completed", "ok"),),
        (ReviewFinding("bug", "null", "bugs"),),
        ("comment-1",),
        "abc1234",
    ),
    ReviewFailed("no diff"),
    Finalized(
        ready=False,
        pull_request="https://example.test/pull/1",
        blockers=("the review did not run",),
    ),
    PollStarted(3),
    PullRequestPolled(
        TARGET,
        7,
        {
            "pr": {"head": {"sha": "abc1234", "ref": "feature"}},
            "threads": [],
            "other": [],
        },
    ),
    FeedbackBatch(
        "abc1234",
        True,
        (
            {
                "key": "thread:t1:c1",
                "kind": "thread",
                "line": None,
                "comments": [{"id": "c1"}],
                "status": "pending",
            },
        ),
    ),
    FeedbackGrouped(
        "1f2e3d",
        "abc1234",
        (
            GroupedIssue("cart", "duplicate products", ("thread:t1:c1",)),
            GroupedIssue("docs", None, ("comment:5:x",)),
        ),
    ),
    IssueResolved(
        "1f2e3d", "cart", "fix-needed", (ReplyDraft("thread:t1:c1", "Fixed!"),)
    ),
    IssueUnclear(
        "1f2e3d",
        "docs",
        "which docs?",
        (ReplyDraft("comment:5:x", "Could you say which?"),),
    ),
    FeedbackHandled(("thread:t1:c1", "comment:5:x"), "def5678"),
    ChecksPassed(),
    OriginValidated("owner/repo"),
    PullRequestClosed("abc1234", merged=True),
    Unsupported("fork head"),
    CiFailing("abc1234"),
    MergeConflict("abc1234", "release", "def5678"),
    MergePlanned("abc1234", "abc1234", "release", "def5678"),
    MergePrepared(("cart.py",)),
    MergeVerified("tree1234"),
    MergePublished("merge1234"),
    CopilotDue("abc1234"),
    ReadinessCheck("abc1234", ready=False),
    CopilotRequested("abc1234", "unavailable"),
    ReadinessReported("abc1234", ready=True),
    PrBlocked("fingerprint", "detail"),
    Transient("flaky runner"),
    BranchBroken("duplicate product"),
    CiRerun("abc1234", "abc1234:digest", (11, 10)),
    CiFixed("abc1234", "repaired"),
]


@pytest.mark.parametrize("event", SAMPLES, ids=lambda event: type(event).__name__)
def test_an_event_round_trips_through_the_ledger_codec(event: Event) -> None:
    """Given an Event, when it is encoded and decoded as its own type, then the same Event comes back."""
    assert decode(encode(event), type(event)) == event


@pytest.mark.parametrize(
    ("event", "sum_type"),
    [
        (Transient("x"), Diagnosis),
        (BranchBroken("y"), Diagnosis),
        (PullRequestClosed("h", merged=False), NextAction),
        (FeedbackBatch("h", False, ()), NextAction),
        (ReadinessCheck("h", ready=True), NextAction),
        (MergeConflict("h", "main", "b"), NextAction),
    ],
    ids=lambda value: getattr(value, "__name__", type(value).__name__),
)
def test_an_event_of_a_closed_sum_decodes_as_the_sum(event: Event, sum_type) -> None:
    """Given an Event stored by a Stage that produces a closed sum, when decoded as the sum, then the variant it was stored as comes back."""
    assert decode(encode(event), sum_type) == event


def test_every_event_class_is_frozen_and_has_a_round_trip_sample() -> None:
    """Given every Event defined in the events package, when scanned, then each is a frozen dataclass with a sample above."""
    defined = {
        member
        for module in pkgutil.iter_modules(events.__path__)
        for _, member in inspect.getmembers(
            importlib.import_module(f"{events.__name__}.{module.name}"), inspect.isclass
        )
        if issubclass(member, Event)
        and member is not Event
        and member.__module__.startswith(events.__name__)
    }

    assert all(
        dataclasses.is_dataclass(event) and event.__dataclass_params__.frozen
        for event in defined
    )
    assert defined == {type(sample) for sample in SAMPLES}
