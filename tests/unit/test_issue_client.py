from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from conftest import commit_file, git
from workflows.platforms.process import CommandError
from workflows.platforms.work_tracking import GitHubRepo, IssueClient
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def _client(gh: FakeGhCli | Mock | None = None) -> IssueClient:
    return IssueClient(GitHubRepo("owner", "repo", gh=gh or FakeGhCli()))


def test_spec_issues_returns_the_raw_nodes_of_open_spec_issues() -> None:
    nodes = _client().spec_issues()

    assert [(node["number"], node["title"]) for node in nodes] == [(1, "Add login page"), (2, "Add logout button")]


def test_sub_issues_returns_the_raw_nodes_of_every_sub_issue() -> None:
    nodes = _client().sub_issues(1)

    assert [(node["number"], node["state"]) for node in nodes] == [
        (10, "OPEN"),
        (11, "OPEN"),
        (12, "OPEN"),
        (13, "CLOSED"),
    ]


def test_ticket_writes_map_to_the_matching_gh_command() -> None:
    gh = FakeGhCli()
    client = _client(gh)

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

    with pytest.raises(CommandError, match="not found"):
        _client(gh).comment(1, "x")


def test_from_origin_reads_the_origin_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    commit_file(repo, "README.md", "hello\n", "initial")
    git(repo, "remote", "add", "origin", "https://example.com/owner/repo.git")
    with pytest.raises(ValueError, match="unsupported remote"):
        GitHubRepo.from_origin(repo)

    git(repo, "remote", "set-url", "origin", "git@github.com:acme/harness.git")
    github_repo = GitHubRepo.from_origin(repo)

    assert github_repo.slug == "acme/harness"
