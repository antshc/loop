from __future__ import annotations

import json

import pytest

from workflows.shared.github.client import GitHub, GitHubRepo
from workflows.shared.github.pull_requests import PullRequests


class Gh:
    """Answers each `gh` call with `respond(args)` and records it."""

    def __init__(self, respond) -> None:
        self.respond = respond
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        return self.respond(args)


def _pulls(gh: Gh) -> PullRequests:
    return PullRequests(GitHubRepo("a", "b", gh=gh))


def test_rest_collection_paginates_and_filters_author() -> None:
    payload = [[{"user": {"login": "Me"}, "number": 1}], [{"user": {"login": "other"}, "number": 2}]]
    gh = Gh(lambda args: json.dumps(payload))

    assert [pr["number"] for pr in _pulls(gh).open_by("me")] == [1]
    assert "--paginate" in gh.calls[0] and "--slurp" in gh.calls[0]


def test_thread_and_comment_pagination_omits_empty_initial_cursor() -> None:
    def respond(args):
        if any("query=query($id" in arg for arg in args):
            return json.dumps({"data": {"node": {"comments": {"nodes": [{"id": "c2"}],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})
        if "cursor=next-thread" in args:
            return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}})
        assert "cursor=" not in args
        return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "t1", "comments": {"nodes": [{"id": "c1"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "next-comment"}}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "next-thread"}}}}}})
    gh = Gh(respond)

    threads = _pulls(gh).threads(1)

    assert [c["id"] for c in threads[0]["comments"]] == ["c1", "c2"]
    assert len(gh.calls) == 3


def test_graphql_mutation_errors_cannot_be_marked_successful() -> None:
    gh = Gh(lambda args: '{"errors":[{"message":"forbidden"}]}')

    with pytest.raises(ValueError, match="forbidden"):
        _pulls(gh).reply(1, {"kind": "thread", "thread_id": "t1"}, "Fixed!")


def test_a_draft_pull_request_is_reused_for_its_head_branch() -> None:
    existing = [{"number": 5, "title": "t", "url": "u", "headRefName": "feat", "body": "old"}]
    gh = Gh(lambda args: json.dumps(existing) if args[:2] == ("pr", "list") else "")

    pr = _pulls(gh).create_draft_pull_request("feat", "main", "t", "new")

    assert (pr.number, pr.body) == (5, "new")
    assert [call[:2] for call in gh.calls] == [("pr", "list"), ("pr", "edit")]


def test_a_missing_draft_pull_request_is_created_as_a_draft() -> None:
    gh = Gh(lambda args: "[]" if args[:2] == ("pr", "list") else "https://github.com/a/b/pull/7\n")

    pr = _pulls(gh).create_draft_pull_request("feat", "main", "Title")

    assert (pr.number, pr.url, pr.branch) == (7, "https://github.com/a/b/pull/7", "feat")
    assert gh.calls[-1][-1] == "--draft" and ("--repo", "a/b") == gh.calls[-1][2:4]


def test_the_account_reaches_repositories_by_slug_and_reports_its_login() -> None:
    github = GitHub(Gh(lambda args: '{"login": "me"}'))

    assert github.login() == "me"
    assert github.repository("https://github.com/o/r.git").slug == "o/r"
    with pytest.raises(ValueError):
        github.repository("not-a-slug")
