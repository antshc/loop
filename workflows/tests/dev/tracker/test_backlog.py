from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from workflows.dev.model.handoff import announce_delivered, block, close_ticket, escalate, hand_to_human
from workflows.dev.model.records import Spec
from workflows.dev.tracker.tickets import TicketsTracker
from workflows.tests.shared.fakes import FakeGhCli


def _tracker(gh: FakeGhCli | None = None) -> TicketsTracker:
    return TicketsTracker("owner", "repo", gh=gh or FakeGhCli())


def _spec(title: str = "Add login page", labels: tuple[str, ...] = ()) -> Spec:
    return Spec(number=7, title=title, url="https://github.com/o/r/issues/7", labels=labels)


def test_specs_maps_open_spec_issues_to_entities() -> None:
    specs = list(_tracker().specs())

    assert [(spec.number, spec.title, spec.url, spec.labels) for spec in specs] == [
        (1, "Add login page", "https://github.com/owner/repo/issues/1", ("spec",)),
        (2, "Add logout button", "https://github.com/owner/repo/issues/2", ("spec",)),
    ]


def test_specs_carry_their_body_and_comments() -> None:
    spec = next(iter(_tracker().specs()))

    assert spec.body == "Body of #1"
    assert [(comment.author, comment.body, comment.created_at) for comment in spec.comments] == [
        ("alice", "Comment on #1", "2026-01-01T00:00:00Z")
    ]


def test_specs_carry_only_the_open_unblocked_sub_issues_as_tickets() -> None:
    spec = next(iter(_tracker().specs()))

    assert [ticket.number for ticket in spec.tickets] == [10]
    assert (spec.tickets[0].state, spec.tickets[0].labels) == ("open", ())
    assert spec.has_work


def test_a_spec_has_no_work_when_every_sub_issue_is_blocked() -> None:
    spec = next(iter(_tracker(FakeGhCli(tickets={1: []})).specs()))

    assert spec.tickets == ()
    assert not spec.has_work


def test_spec_is_awaiting_a_human_only_with_the_hitl_label() -> None:
    assert _spec(labels=("spec", "hitl")).awaiting_human
    assert not _spec(labels=("spec",)).awaiting_human


def test_a_comment_whose_author_account_was_deleted_is_attributed_to_ghost() -> None:
    node = {
        "number": 1,
        "title": "Add login page",
        "url": "https://github.com/owner/repo/issues/1",
        "state": "OPEN",
        "body": "",
        "labels": {"nodes": [{"name": "spec"}]},
        "comments": {"nodes": [{"author": None, "body": "hi", "createdAt": "2026-01-01T00:00:00Z"}]},
    }

    spec = next(iter(_tracker(FakeGhCli(specs=[node])).specs()))

    assert spec.comments[0].author == "ghost"


def _writes(gh: FakeGhCli) -> list[tuple[str, ...]]:
    return [call[1:] for call in gh.writes()]


def test_hand_to_human_labels_then_comments_immediately() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    handed = hand_to_human(tracker, spec, "stuck")

    assert handed.awaiting_human and not spec.awaiting_human
    assert [(call[0], call[1], call[-2]) for call in _writes(gh)] == [
        ("edit", "1", "--add-label"),
        ("comment", "1", "--body"),
    ]
    assert _writes(gh)[0][-1] == "hitl"
    assert _writes(gh)[1][-1].startswith("stuck")


def test_block_hands_the_spec_to_a_human_only_when_it_has_tickets() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    empty = block(tracker, replace(spec, tickets=()), "nothing blocked")
    assert gh.writes() == [] and not empty.awaiting_human

    blocked = block(tracker, spec, "blocked")
    assert blocked.awaiting_human
    assert _writes(gh)[-1][-1].startswith("blocked")


def test_close_ticket_closes_it_on_github_and_in_the_returned_snapshot() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    closed = close_ticket(tracker, spec, 10, "done")

    assert spec.tickets[0].state == "open" and spec.has_work
    assert closed.tickets[0].state == "closed" and not closed.has_work
    assert _writes(gh) == [("close", "10", "--repo", "owner/repo", "--comment", "done")]


def test_escalate_hands_the_ticket_then_the_spec_to_a_human_in_that_order() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    escalated = escalate(tracker, spec, 10, "boom")

    assert ("hitl" in escalated.tickets[0].labels) and escalated.awaiting_human
    assert [call[1] for call in _writes(gh)] == ["10", "10", "1", "1"]
    assert _writes(gh)[-1][-1].startswith("dev: boom")


def test_announce_delivered_comments_the_pull_request_on_the_spec() -> None:
    gh = FakeGhCli()

    tracker = _tracker(gh)

    announce_delivered(tracker, next(iter(tracker.specs())), "https://github.com/o/r/pull/3")

    [write] = _writes(gh)
    assert write[:2] == ("comment", "1")
    assert write[-1].startswith("dev: all Tickets delivered; draft pull request: https://github.com/o/r/pull/3")


def test_a_ticket_outside_the_spec_cannot_be_changed_through_it() -> None:
    gh = FakeGhCli()

    with pytest.raises(ValueError):
        close_ticket(_tracker(gh), _spec(), 99, "done")

    assert gh.writes() == []


def test_a_spec_snapshot_is_immutable() -> None:
    spec = _spec()

    with pytest.raises(FrozenInstanceError):
        spec.labels = ("hitl",)  # type: ignore[misc]


def test_spec_initiative_and_bare_title_split_on_the_first_colon() -> None:
    spec = _spec("Checkout: Add login page")

    assert (spec.initiative, spec.bare_title) == ("Checkout", "Add login page")


def test_spec_initiative_falls_back_to_its_number_without_a_colon_prefix() -> None:
    spec = _spec("Add login page")

    assert (spec.initiative, spec.bare_title) == ("7", "Add login page")


def test_spec_target_reads_the_single_repo_target_value() -> None:
    assert _spec(labels=("spec", "repo:target:owner/name")).target == "owner/name"


@pytest.mark.parametrize(
    "labels",
    [
        (),
        ("repo:target:not-a-slug",),
        ("repo:target:owner/name", "repo:target:owner/other"),
        ("repo:target:github.com/owner/name",),
    ],
)
def test_spec_target_is_none_when_missing_malformed_or_duplicated(labels: tuple[str, ...]) -> None:
    assert _spec(labels=labels).target is None


def test_spec_base_branch_reads_the_single_repo_base_value() -> None:
    assert _spec(labels=("repo:base:main",)).base_branch == "main"


@pytest.mark.parametrize("labels", [(), ("repo:base:",), ("repo:base:a", "repo:base:b")])
def test_spec_base_branch_is_none_when_missing_empty_or_duplicated(labels: tuple[str, ...]) -> None:
    assert _spec(labels=labels).base_branch is None
