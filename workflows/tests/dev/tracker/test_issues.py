from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from workflows.tests.dev.doubles import commit_file, git
from workflows.dev.model.records import Spec
from workflows.dev.tracker.tickets import TicketsTracker
from workflows.shared.git import GitClient
from workflows.shared.github.repo import GitHubRepo
from workflows.shared.process import CommandError, CommandRunner
from workflows.tests.shared.fakes import FakeGhCli


def _tracker(gh: FakeGhCli | Mock | None = None) -> TicketsTracker:
    return TicketsTracker("owner", "repo", gh=gh or FakeGhCli())


def _spec() -> Spec:
    return Spec(number=1, title="t", url="u", labels=())


def test_ticket_writes_map_to_the_matching_gh_command() -> None:
    gh = FakeGhCli()
    spec = _spec()
    spec.comment("hello")
    spec.hand_to_human("done")

    _tracker(gh).update_spec(spec)

    assert gh.calls == [
        ("issue", "comment", "1", "--repo", "owner/repo", "--body", "hello"),
        ("issue", "edit", "1", "--repo", "owner/repo", "--add-label", "hitl"),
        ("issue", "comment", "1", "--repo", "owner/repo", "--body", "done"),
    ]


def test_non_zero_gh_exit_raises_a_command_error_with_the_command_and_output() -> None:
    gh = Mock(side_effect=CommandError("gh issue comment 1", 1, "not found"))

    spec = _spec()
    spec.comment("x")

    with pytest.raises(CommandError, match="not found"):
        _tracker(gh).update_spec(spec)


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
