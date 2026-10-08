from __future__ import annotations

from dataclasses import replace

import pytest

from loop import InMemoryExecutionStore
from workflows.platforms.work_tracking import GitHubClient, Spec, TicketsTracker
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def _tracker(gh: FakeGhCli | None = None) -> TicketsTracker:
    return TicketsTracker(GitHubClient("owner", "repo", gh=gh or FakeGhCli()), InMemoryExecutionStore(), "owner/repo")


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


def test_hitl_labels_then_comments_on_the_issue() -> None:
    gh = FakeGhCli()

    _tracker(gh).hitl(5, "stuck")

    assert gh.calls == [
        ("issue", "edit", "5", "--repo", "owner/repo", "--add-label", "hitl"),
        ("issue", "comment", "5", "--repo", "owner/repo", "--body", "stuck"),
    ]


def test_block_spec_hands_the_spec_to_a_human_only_when_it_has_tickets() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    calls_before = len(gh.calls)
    tracker.block_spec(replace(spec, tickets=()), "nothing blocked")
    assert len(gh.calls) == calls_before

    tracker.block_spec(spec, "blocked")
    assert gh.calls[-1] == ("issue", "comment", "1", "--repo", "owner/repo", "--body", "blocked")


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
