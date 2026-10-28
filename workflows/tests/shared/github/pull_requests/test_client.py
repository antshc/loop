from __future__ import annotations

import pytest

from workflows.shared.github.pull_requests import PullRequests as PullRequestClient
from workflows.shared.github.repo import GitHubRepo
from workflows.tests.shared.fakes import PR_DIFF, FakeGhCli, FakePullRequestHost, comment_node, thread_node


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


def test_reusing_a_pull_request_refreshes_its_ticket_checklist() -> None:
    """Given an existing draft, when more Tickets land, then its body reflects the latest delivery."""
    gh = FakeGhCli()
    client = _client(gh)
    first = client.create_draft_pull_request("feature/basket", "main", "Basket", "- [x] #1\n- [ ] #2")
    updated = client.create_draft_pull_request("feature/basket", "main", "Basket", "- [x] #1\n- [x] #2")
    assert first.number == updated.number == 99
    assert len([call for call in gh.calls if call[:2] == ("pr", "create")]) == 1
    assert gh.calls[-1] == ("pr", "edit", "99", "--repo", "owner/repo", "--body", "- [x] #1\n- [x] #2")


def test_an_open_pull_request_is_found_by_its_head_branch() -> None:
    found = _client().find_open("feature/login")

    assert found is not None and (found.number, found.branch) == (10, "feature/login")
    assert _client().find_open("feature/unknown") is None


def test_a_pull_request_is_marked_ready_and_diffed_by_number_or_url() -> None:
    gh = FakeGhCli()
    client = _client(gh)

    client.mark_ready("https://github.com/owner/repo/pull/10")

    assert client.diff("10") == PR_DIFF
    assert gh.calls == [
        ("pr", "ready", "https://github.com/owner/repo/pull/10", "--repo", "owner/repo"),
        ("pr", "diff", "10", "--repo", "owner/repo"),
    ]


def _host_client() -> tuple[PullRequestClient, FakePullRequestHost, FakeGhCli]:
    host = FakePullRequestHost()
    host.add(1).threads = [thread_node([comment_node("c1", "reviewer", "Check this")])]
    gh = FakeGhCli(pulls=host)
    return PullRequestClient(GitHubRepo("a", "b", gh=gh)), host, gh


def test_a_reply_lands_in_its_review_thread_and_leaves_it_open() -> None:
    client, host, _ = _host_client()

    client.reply_to_thread("t1", "Fixed!")

    (thread,) = client.threads(1)
    assert [comment.body for comment in thread.comments] == ["Check this", "Fixed!"] and not thread.resolved


def test_a_rejected_thread_reply_cannot_be_taken_for_a_success() -> None:
    client, host, _ = _host_client()
    host.reply_failure = "forbidden"

    with pytest.raises(ValueError, match="forbidden"):
        client.reply_to_thread("t1", "Fixed!")


def test_a_pull_request_comment_is_posted_to_its_conversation() -> None:
    client, host, gh = _host_client()

    client.comment(1, "In response to the docs request")

    assert [comment.body for comment in client.snapshot(1).comments] == ["In response to the docs request"]
    assert gh.calls[0] == ("api", "repos/a/b/issues/1/comments", "-X", "POST", "-f", "body=In response to the docs request")


def test_failed_runs_are_logged_and_rerun_by_id() -> None:
    client, host, gh = _host_client()

    logs = client.failed_logs([11, 12])
    client.rerun_failed(11)

    assert logs == f"{host.logs}\n{host.logs}"
    assert host.retries == [11]
    assert gh.calls[-1] == ("run", "rerun", "11", "-R", "a/b", "--failed")


def test_a_copilot_review_is_requested_for_a_pull_request() -> None:
    client, host, _ = _host_client()

    client.request_copilot(1)

    assert host.requested == [1]
