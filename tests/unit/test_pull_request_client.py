from __future__ import annotations

from workflows.platforms.work_tracking import GitHubRepo, PullRequestClient
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def _client(gh: FakeGhCli | None = None) -> PullRequestClient:
    return PullRequestClient(GitHubRepo("owner", "repo", gh=gh or FakeGhCli()))


def test_find_pull_request_does_not_issue_a_create_call() -> None:
    gh = FakeGhCli()

    pull_request = _client(gh).find_pull_request("feature/login")

    assert pull_request is not None and (pull_request.number, pull_request.branch) == (10, "feature/login")
    assert all(call[:2] != ("pr", "create") for call in gh.calls)


def test_find_pull_request_returns_none_for_an_unknown_branch() -> None:
    assert _client().find_pull_request("no-such-branch") is None


def test_review_threads_reads_each_thread_with_its_resolution() -> None:
    threads = _client().review_threads("10")

    assert [(thread.id, thread.resolved) for thread in threads] == [("t1", False), ("t2", True)]


def test_create_draft_pull_request_asks_gh_for_a_draft_from_head_to_base() -> None:
    gh = FakeGhCli()

    _client(gh).create_draft_pull_request("feature/x", "main", "My PR", "body")

    assert gh.calls[-1] == (
        "pr", "create",
        "--repo", "owner/repo",
        "--head", "feature/x",
        "--base", "main",
        "--title", "My PR",
        "--body", "body",
        "--draft",
    )
