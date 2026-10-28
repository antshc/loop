from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from workflows.tests.dev.doubles import commit_file, git
from workflows.dev.model.handoff import hand_to_human
from workflows.dev.model.records import Spec
from workflows.dev.tracker.tickets import TicketsTracker
from workflows.shared.git import GitClient
from workflows.shared.github.repo import GitHubRepo
from workflows.shared.process import CommandError, CommandRunner
from workflows.tests.shared.fakes import FakeGhCli, issue


def _tracker(gh: FakeGhCli | Mock | None = None) -> TicketsTracker:
    return TicketsTracker("owner", "repo", gh=gh or FakeGhCli())


def _spec() -> Spec:
    return Spec(number=1, title="t", url="u", labels=())


def test_each_command_is_a_gh_write_sent_in_the_order_issued() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)

    tracker.add_label(1, "hitl")
    tracker.comment(1, "hand-off", "hello")
    tracker.remove_label(1, "spec")
    tracker.close(10, "done")

    assert [call[:3] for call in gh.writes()] == [
        ("issue", "edit", "1"),
        ("issue", "comment", "1"),
        ("issue", "edit", "1"),
        ("issue", "close", "10"),
    ]
    assert [call[-2:] for call in gh.writes()][0] == ("--add-label", "hitl")
    assert [call[-2:] for call in gh.writes()][2] == ("--remove-label", "spec")


def test_a_change_is_on_github_before_the_next_step_reads_it() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)

    tracker.add_label(1, "hitl")
    tracker.comment(1, "hand-off", "hello")
    spec = next(iter(tracker.specs()))

    assert "hitl" in spec.labels
    assert spec.comments[-1].body.startswith("hello")


def test_a_failing_write_raises_where_it_happens_and_keeps_the_earlier_writes() -> None:
    gh = FakeGhCli()
    gh.fail_on("issue", "comment")
    tracker = _tracker(gh)

    with pytest.raises(CommandError, match="scheduled failure"):
        hand_to_human(tracker, _spec(), "stuck")

    assert [call[1] for call in gh.writes()] == ["edit", "comment"]
    assert "hitl" in next(iter(tracker.specs())).labels
    assert next(iter(tracker.specs())).comments[-1].body == "Comment on #1"


def test_a_retry_rereads_state_and_skips_the_label_already_applied() -> None:
    gh = FakeGhCli()
    gh.fail_on("issue", "comment")
    tracker = _tracker(gh)
    with pytest.raises(CommandError):
        hand_to_human(tracker, _spec(), "stuck")
    views_before = len([call for call in gh.calls if call[:2] == ("issue", "view")])

    hand_to_human(tracker, _spec(), "stuck")

    assert len([call for call in gh.calls if call[:2] == ("issue", "view")]) > views_before
    assert [call[-2:] for call in gh.writes() if "--add-label" in call] == [("--add-label", "hitl")]
    assert [call[1] for call in gh.writes()] == ["edit", "comment", "comment"]


def test_a_removed_label_that_is_already_absent_sends_no_write() -> None:
    gh = FakeGhCli()

    _tracker(gh).remove_label(1, "hitl")

    assert gh.writes() == []


def test_a_comment_whose_fingerprint_is_already_on_the_issue_is_not_posted_twice() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)

    tracker.comment(1, "verification", "dev: app verification **pass**")
    tracker.comment(1, "verification", "dev: app verification **pass**")
    tracker.comment(1, "verification", "dev: app verification **fail**")
    tracker.comment(1, "review-summary", "dev: app verification **pass**")

    assert [call[-1].split("\n")[0] for call in gh.writes()] == [
        "dev: app verification **pass**",
        "dev: app verification **fail**",
        "dev: app verification **pass**",
    ]


def test_a_closed_issue_is_not_closed_again() -> None:
    gh = FakeGhCli()
    tracker = _tracker(gh)

    tracker.close(10, "done")
    tracker.close(10, "done again")

    assert [call[1:3] for call in gh.writes()] == [("close", "10")]


def test_a_crash_between_creating_and_linking_a_ticket_is_completed_on_retry_without_a_second_ticket() -> None:
    gh = FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=("spec", "repo:target:o/r"))])
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))
    gh.fail_on("api", "--method")

    created = tracker.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")
    with pytest.raises(CommandError):
        tracker.link_ticket(spec, created)

    found = tracker.find_ticket(spec, "0-add-basket")
    assert found is not None and found.number == created.number
    tracker.link_ticket(spec, found)

    assert len(gh.calls_matching("issue", "create")) == 1
    assert [ticket.number for ticket in next(iter(tracker.specs())).tickets] == [created.number]


def test_a_ticket_already_linked_to_its_spec_is_not_linked_again() -> None:
    gh = FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=("spec", "repo:target:o/r"))])
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))
    created = tracker.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    tracker.link_ticket(spec, created)
    tracker.link_ticket(spec, created)

    assert len([call for call in gh.writes() if call[:2] == ("api", "--method")]) == 1


def test_find_ticket_ignores_other_keys_other_specs_and_closed_issues() -> None:
    gh = FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=("spec", "repo:target:o/r"))])
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))
    created = tracker.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    assert tracker.find_ticket(spec, "1-add-coffee") is None
    assert tracker.find_ticket(replace(spec, number=2), "0-add-basket") is None
    tracker.close(created.number, "done")
    assert tracker.find_ticket(spec, "0-add-basket") is None


def test_a_created_ticket_carries_the_routing_label_and_a_hidden_marker() -> None:
    gh = FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=("spec", "repo:target:o/r"))])
    tracker = _tracker(gh)
    spec = next(iter(tracker.specs()))

    created = tracker.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    [call] = gh.calls_matching("issue", "create")
    assert call[call.index("--label") + 1] == "repo:target:o/r"
    assert created.body == "Create a basket\n\n<!-- loop:ticket spec=1 key=0-add-basket -->"


def test_non_zero_gh_exit_raises_a_command_error_with_the_command_and_output() -> None:
    gh = Mock(side_effect=CommandError("gh issue view 1", 1, "not found"))

    with pytest.raises(CommandError, match="not found"):
        _tracker(gh).comment(1, "hand-off", "x")


def test_from_origin_reads_the_origin_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    commit_file(repo, "README.md", "hello\n", "initial")
    git(repo, "remote", "add", "origin", "https://example.com/owner/repo.git")
    with pytest.raises(ValueError, match="unsupported"):
        GitHubRepo.from_slug(GitClient(CommandRunner()).origin_slug(repo), FakeGhCli())

    git(repo, "remote", "set-url", "origin", "git@github.com:acme/harness.git")
    github_repo = GitHubRepo.from_slug(GitClient(CommandRunner()).origin_slug(repo), FakeGhCli())

    assert github_repo.slug == "acme/harness"
