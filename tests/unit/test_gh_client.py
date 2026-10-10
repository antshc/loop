from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from conftest import commit_file, git
from workflows.platforms.process import CommandError
from workflows.platforms.work_tracking import GitHubClient
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def test_spec_issues_returns_the_raw_nodes_of_open_spec_issues() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    nodes = client.spec_issues()

    assert [(node["number"], node["title"]) for node in nodes] == [(1, "Add login page"), (2, "Add logout button")]


def test_sub_issues_returns_the_raw_nodes_of_every_sub_issue() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    nodes = client.sub_issues(1)

    assert [(node["number"], node["state"]) for node in nodes] == [
        (10, "OPEN"),
        (11, "OPEN"),
        (12, "OPEN"),
        (13, "CLOSED"),
    ]


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


def test_for_repo_reads_the_origin_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "remote", "add", "origin", "https://example.com/owner/repo.git")
    with pytest.raises(ValueError, match="unsupported remote"):
        GitHubClient.for_repo(repo)

    git(repo, "remote", "set-url", "origin", "git@github.com:owner/repo.git")
    harness, target = GitHubClient.for_repo(repo)
    assert isinstance(harness, GitHubClient) and target is harness


def test_find_pull_request_does_not_issue_a_create_call() -> None:
    gh = FakeGhCli()
    client = GitHubClient("owner", "repo", gh=gh)

    pull_request = client.find_pull_request("feature/login")

    assert pull_request is not None and (pull_request.number, pull_request.branch) == (10, "feature/login")
    assert all(call[:2] != ("pr", "create") for call in gh.calls)


def test_find_pull_request_returns_none_for_an_unknown_branch() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    assert client.find_pull_request("no-such-branch") is None


def test_review_threads_reads_each_thread_with_its_resolution() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    threads = client.review_threads("10")

    assert [(thread.id, thread.resolved) for thread in threads] == [("t1", False), ("t2", True)]


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


def test_non_zero_gh_exit_raises_a_command_error_with_the_command_and_output() -> None:
    gh = Mock(side_effect=CommandError("gh issue comment 1", 1, "not found"))
    client = GitHubClient("owner", "repo", gh=gh)

    with pytest.raises(CommandError, match="not found"):
        client.comment(1, "x")
