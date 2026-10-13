from __future__ import annotations

from workflows.platforms.work_tracking import GitHubRepo, PullRequestClient
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def _client(gh: FakeGhCli | None = None) -> PullRequestClient:
    return PullRequestClient(GitHubRepo("owner", "repo", gh=gh or FakeGhCli()))


def test_create_draft_pull_request_reuses_an_open_pr_without_creating_another() -> None:
    gh = FakeGhCli()

    pull_request = _client(gh).create_draft_pull_request("feature/login", "main", "My PR")

    assert (pull_request.number, pull_request.branch) == (10, "feature/login")
    assert pull_request.url == "https://github.com/owner/repo/pull/10"
    assert all(call[:2] != ("pr", "create") for call in gh.calls)


def test_create_draft_pull_request_asks_gh_for_a_draft_from_head_to_base() -> None:
    gh = FakeGhCli()

    pull_request = _client(gh).create_draft_pull_request("feature/x", "main", "My PR", "body")

    assert (pull_request.number, pull_request.title, pull_request.branch) == (99, "My PR", "feature/x")
    assert pull_request.url == "https://github.com/owner/repo/pull/99"
    assert gh.calls[-1] == (
        "pr", "create",
        "--repo", "owner/repo",
        "--head", "feature/x",
        "--base", "main",
        "--title", "My PR",
        "--body", "body",
        "--draft",
    )
