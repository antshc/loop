from __future__ import annotations

from dataclasses import replace

import pytest

from software_factory.legacy_slices.implement_spec.model.handoff import announce_delivered, block, close_ticket, escalate, hand_to_human
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.repo import GitHubRepo
from software_factory.tests.shared.builders import build_graph, build_ticket
from software_factory.tests.shared.fakes import FakeGhCli


def _tracker(gh: FakeGhCli) -> Issues:
    return Issues(GitHubRepo("owner", "repo", gh=gh))


def test_hand_to_human_labels_then_comments_immediately() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    handed = hand_to_human(tracker, spec, "stuck")

    assert handed.awaiting_human and not spec.awaiting_human
    assert [call[:3] for call in gh.writes()] == [("issue", "edit", "1"), ("api", "--method", "POST")]
    assert gh.writes()[0][-1] == "hitl"
    assert [body.split("\n")[0] for _, body in gh.posted_comments()] == ["stuck"]


def test_block_hands_the_spec_to_a_human_only_when_it_has_tickets() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    empty = block(tracker, replace(spec, tickets=()), "nothing blocked")
    assert gh.writes() == [] and not empty.awaiting_human

    blocked = block(tracker, spec, "blocked")
    assert blocked.awaiting_human
    assert gh.posted_comments()[-1][1].startswith("blocked")


def test_close_ticket_closes_it_on_github_and_in_the_returned_snapshot() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    closed = close_ticket(tracker, spec, 10, "done")

    assert spec.tickets[0].state == "open" and spec.has_work
    assert closed.tickets[0].state == "closed" and not closed.has_work
    assert gh.writes() == [("issue", "close", "10", "--repo", "owner/repo", "--comment", "done")]


def test_escalate_hands_the_ticket_then_the_spec_to_a_human_in_that_order() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    escalated = escalate(tracker, spec, 10, "boom")

    assert ("hitl" in escalated.tickets[0].labels) and escalated.awaiting_human
    assert [call[2] for call in gh.writes() if call[0] == "issue"] == ["10", "1"]
    assert [number for number, _ in gh.posted_comments()] == [10, 1]
    assert gh.posted_comments()[-1][1].startswith("implement_spec: boom")


def test_announce_delivered_comments_the_pull_request_on_the_spec() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)

    announce_delivered(tracker, next(iter(tracker.specs())), "https://github.com/o/r/pull/3")

    [(number, body)] = gh.posted_comments()
    assert number == 1
    assert body.startswith("implement_spec: all Tickets delivered; draft pull request: https://github.com/o/r/pull/3")


def test_a_ticket_outside_the_spec_cannot_be_changed_through_it() -> None:
    gh = FakeGhCli()

    with pytest.raises(ValueError):
        close_ticket(_tracker(gh), build_graph(tickets=(build_ticket(10),)), 99, "done")

    assert gh.writes() == []
