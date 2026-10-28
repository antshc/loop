from __future__ import annotations

import pytest

from workflows.shared.github.pull_requests.model import PullRequestState
from workflows.shared.process import CommandError

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


def test_a_thread_is_resolved_and_unresolved_where_it_was_replied_to() -> None:
    client, _, gh = _host_client()

    client.resolve_thread("t1")
    resolved = client.threads(1)[0].resolved
    client.unresolve_thread("t1")
    unresolved = client.threads(1)[0].resolved

    assert (resolved, unresolved) == (True, False)
    mutations = [call for call in gh.calls_matching("api", "graphql") if any("ReviewThread(" in arg for arg in call)]
    assert [any(f"{name}(" in arg for arg in call) for call, name in zip(mutations, ("resolveReviewThread", "unresolveReviewThread"))] == [True, True]
    assert all("id=t1" in call for call in mutations) and len(mutations) == 2


def test_a_rejected_thread_resolution_cannot_be_taken_for_a_success() -> None:
    client, host, _ = _host_client()
    host.thread_failure = "forbidden"

    with pytest.raises(ValueError, match="forbidden"):
        client.resolve_thread("t1")
    with pytest.raises(ValueError, match="forbidden"):
        client.unresolve_thread("t1")


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


def test_a_comment_is_a_conversation_comment_and_not_a_review_comment() -> None:
    client, _, gh = _host_client()

    client.comment(1, "Heads up")

    assert [thread.comments[-1].body for thread in client.threads(1)] == ["Check this"]
    assert gh.calls[0][1] == "repos/a/b/issues/1/comments"


def test_the_description_is_replaced_by_the_new_body() -> None:
    client, host, gh = _host_client()

    client.update_description(1, "New body")

    assert client.pull_request(1).body == "New body" == host.pulls[1].body
    assert gh.calls[0] == ("pr", "edit", "1", "--repo", "a/b", "--body", "New body")


def test_a_pull_request_is_closed_and_reopened() -> None:
    client, _, gh = _host_client()

    client.close(1)
    closed = client.pull_request(1).state
    client.reopen(1)
    reopened = client.pull_request(1).state

    assert (closed, reopened) == (PullRequestState.CLOSED, PullRequestState.OPEN)
    assert gh.writes() == [("pr", "close", "1", "--repo", "a/b"), ("pr", "reopen", "1", "--repo", "a/b")]


@pytest.mark.parametrize(
    ("command", "failing"),
    [
        (lambda client: client.reply_to_thread("t1", "x"), ("api", "graphql")),
        (lambda client: client.resolve_thread("t1"), ("api", "graphql")),
        (lambda client: client.unresolve_thread("t1"), ("api", "graphql")),
        (lambda client: client.comment(1, "x"), ("api", "repos/a/b/issues/1/comments")),
        (lambda client: client.update_description(1, "x"), ("pr", "edit")),
        (lambda client: client.close(1), ("pr", "close")),
        (lambda client: client.reopen(1), ("pr", "reopen")),
    ],
    ids=["reply", "resolve", "unresolve", "comment", "description", "close", "reopen"],
)
def test_a_failed_write_raises_from_the_command(command, failing) -> None:
    client, host, gh = _host_client()
    gh.fail_on(*failing)

    with pytest.raises(CommandError):
        command(client)

    assert host.replies == [] and host.pulls[1].body == "Body" and host.pulls[1].state == "open"


def test_a_draft_whose_pull_request_exists_with_a_different_body_overwrites_it_without_a_conflict_check() -> None:
    gh = FakeGhCli()
    client = _client(gh)
    created = client.create_draft_pull_request("feature/basket", "main", "Basket", "Edited by a human")
    gh.calls.clear()

    pull_request = client.create_draft_pull_request("feature/basket", "main", "Basket", "Desired")

    assert (pull_request.number, pull_request.body) == (created.number, "Desired")
    assert gh.writes() == [("pr", "edit", "99", "--repo", "owner/repo", "--body", "Desired")]


def test_a_draft_whose_pull_request_already_has_the_body_sends_no_write() -> None:
    gh = FakeGhCli()
    client = _client(gh)
    client.create_draft_pull_request("feature/basket", "main", "Basket", "Desired")
    gh.calls.clear()

    client.create_draft_pull_request("feature/basket", "main", "Basket", "Desired")

    assert gh.writes() == []


def test_a_missing_draft_is_created_with_one_write() -> None:
    gh = FakeGhCli()

    _client(gh).create_draft_pull_request("feature/new", "main", "New", "Desired")

    assert [write[:2] for write in gh.writes()] == [("pr", "create")] and "--draft" in gh.writes()[0]


def test_the_commands_include_no_merge() -> None:
    public = {name for name in dir(PullRequestClient) if not name.startswith("_")}

    assert not [name for name in public if "merge" in name.lower()]
    assert {"reply_to_thread", "resolve_thread", "unresolve_thread", "comment", "update_description", "close", "reopen"} <= public
