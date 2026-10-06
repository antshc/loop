from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from conftest import commit_file, git
from orb import GitHubClient
from orb.errors import CommandError
from orb.testing import FakeGhCli


def test_get_specs_returns_only_open_issues_labelled_spec() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    specs = client.get_specs()

    assert [(spec.number, spec.title, spec.url, spec.labels) for spec in specs] == [
        (1, "Add login page", "https://github.com/owner/repo/issues/1", ("spec",)),
        (2, "Add logout button", "https://github.com/owner/repo/issues/2", ("spec",)),
    ]


def test_get_actionable_issues_drops_closed_hitl_and_spec_labelled_sub_issues() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())
    spec = client.get_specs()[0]

    result = client.get_actionable_issues(spec)

    assert [ticket.number for ticket in result] == [10]


def test_get_actionable_issues_returns_empty_when_every_sub_issue_is_blocked() -> None:
    gh = FakeGhCli(tickets={1: []})
    client = GitHubClient("owner", "repo", gh=gh)
    spec = client.get_specs()[0]

    assert client.get_actionable_issues(spec) == []


def test_for_repo_binds_specs_and_tickets_to_the_harness_and_pull_requests_to_the_target(
    tmp_path: Path,
) -> None:
    harness_repo = tmp_path / "harness"
    harness_repo.mkdir()
    git(harness_repo, "init", "-b", "main")
    git(harness_repo, "config", "user.email", "test@example.com")
    git(harness_repo, "config", "user.name", "Test")
    commit_file(harness_repo, "README.md", "hello\n", "initial")
    git(harness_repo, "remote", "add", "origin", "git@github.com:acme/harness.git")

    target_repo = tmp_path / "target"
    target_repo.mkdir()
    git(target_repo, "init", "-b", "main")
    git(target_repo, "config", "user.email", "test@example.com")
    git(target_repo, "config", "user.name", "Test")
    commit_file(target_repo, "README.md", "hello\n", "initial")
    git(target_repo, "remote", "add", "origin", "git@github.com:acme/target.git")

    harness, target = GitHubClient.for_repo(harness_repo, target_repo)

    assert harness is not target
    assert (harness._owner, harness._repo) == ("acme", "harness")
    assert (target._owner, target._repo) == ("acme", "target")


def test_find_pull_request_does_not_issue_a_create_call() -> None:
    gh = FakeGhCli()
    client = GitHubClient("owner", "repo", gh=gh)

    pull_request = client.find_pull_request("feature/login")

    assert pull_request is not None and (pull_request.number, pull_request.branch) == (10, "feature/login")
    assert all(call[:2] != ("pr", "create") for call in gh.calls)


def test_find_pull_request_returns_none_for_an_unknown_branch() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    assert client.find_pull_request("no-such-branch") is None


def test_create_draft_pull_request_asks_gh_for_a_draft_from_head_to_base() -> None:
    gh = FakeGhCli()
    client = GitHubClient("owner", "repo", gh=gh)

    client.create_draft_pull_request("feature/x", "main", "My PR", "body")

    assert gh.calls[-1] == (
        "pr", "create",
        "--repo", "owner/repo",
        "--head", "feature/x",
        "--base", "main",
        "--title", "My PR",
        "--body", "body",
        "--draft",
    )


def test_ticket_writes_map_to_the_matching_gh_command() -> None:
    gh = FakeGhCli()
    client = GitHubClient("owner", "repo", gh=gh)

    client.comment(1, "hello")
    client.add_label(1, "ready")
    client.close_with_comment(1, "done")

    assert gh.calls == [
        ("issue", "comment", "1", "--repo", "owner/repo", "--body", "hello"),
        ("issue", "edit", "1", "--repo", "owner/repo", "--add-label", "ready"),
        ("issue", "close", "1", "--repo", "owner/repo", "--comment", "done"),
    ]


def test_dry_run_suppresses_every_write_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    gh = FakeGhCli()
    client = GitHubClient("owner", "repo", gh=gh, dry_run=True)

    with caplog.at_level("INFO", logger="orb.platforms.github"):
        client.comment(1, "x")
        client.add_label(1, "ready")
        client.close_with_comment(1, "done")
        client.create_draft_pull_request("feature/x", "main", "title")
        client.update_pull_request(1, title="new title")
        client.reply_to_thread("10", "t1", "x")

    assert gh.calls == []
    assert len(caplog.records) == 6


def test_non_zero_gh_exit_raises_a_command_error_with_the_command_and_output() -> None:
    gh = Mock(side_effect=CommandError("gh issue comment 1", 1, "not found"))
    client = GitHubClient("owner", "repo", gh=gh)

    with pytest.raises(CommandError, match="not found"):
        client.comment(1, "x")
