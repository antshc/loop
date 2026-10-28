from __future__ import annotations

import json

import pytest

from workflows.shared.github.account import GitHub
from workflows.shared.github.repo import GitHubRepo
from workflows.shared.github.pull_requests import PullRequests
from workflows.tests.shared.fakes import ScriptedGh


def _pulls(gh: ScriptedGh) -> PullRequests:
    return PullRequests(GitHubRepo("a", "b", gh=gh))


def test_rest_collection_paginates_and_filters_author() -> None:
    payload = [[{"user": {"login": "Me"}, "number": 1}], [{"user": {"login": "other"}, "number": 2}]]
    gh = ScriptedGh(lambda args: json.dumps(payload))

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
    gh = ScriptedGh(respond)

    threads = _pulls(gh).threads(1)

    assert [c["id"] for c in threads[0]["comments"]] == ["c1", "c2"]
    assert len(gh.calls) == 3


def test_graphql_mutation_errors_cannot_be_marked_successful() -> None:
    gh = ScriptedGh(lambda args: '{"errors":[{"message":"forbidden"}]}')

    with pytest.raises(ValueError, match="forbidden"):
        _pulls(gh).reply(1, {"kind": "thread", "thread_id": "t1"}, "Fixed!")


def test_a_draft_pull_request_is_reused_for_its_head_branch() -> None:
    existing = [{"number": 5, "title": "t", "url": "u", "headRefName": "feat", "body": "old"}]
    gh = ScriptedGh(lambda args: json.dumps(existing) if args[:2] == ("pr", "list") else "")

    pr = _pulls(gh).create_draft_pull_request("feat", "main", "t", "new")

    assert (pr.number, pr.body) == (5, "new")
    assert [call[:2] for call in gh.calls] == [("pr", "list"), ("pr", "edit")]


def test_a_missing_draft_pull_request_is_created_as_a_draft() -> None:
    gh = ScriptedGh(lambda args: "[]" if args[:2] == ("pr", "list") else "https://github.com/a/b/pull/7\n")

    pr = _pulls(gh).create_draft_pull_request("feat", "main", "Title")

    assert (pr.number, pr.url, pr.branch) == (7, "https://github.com/a/b/pull/7", "feat")
    assert gh.calls[-1][-1] == "--draft" and ("--repo", "a/b") == gh.calls[-1][2:4]


def test_the_account_reaches_repositories_by_slug_and_reports_its_login() -> None:
    github = GitHub(ScriptedGh(lambda args: '{"login": "me"}'))

    assert github.login() == "me"
    assert github.repository("https://github.com/o/r.git").slug == "o/r"
    with pytest.raises(ValueError):
        github.repository("not-a-slug")


def test_the_account_reads_one_pull_request_of_a_repository() -> None:
    gh = ScriptedGh(lambda args: '{"number": 7, "state": "open"}')

    pull = GitHub(gh).pull_requests("o/r").pull(7)

    assert pull["number"] == 7
    assert gh.calls[-1][:2] == ("api", "repos/o/r/pulls/7")


def test_a_closed_pull_request_snapshot_skips_reviews_and_checks() -> None:
    gh = ScriptedGh(lambda args: '{"number": 7, "state": "closed"}')

    snapshot = _pulls(gh).snapshot(7)

    assert snapshot["pr"]["state"] == "closed" and snapshot["threads"] == [] and snapshot["checks"] == []
    assert len(gh.calls) == 1


def test_a_repository_is_read_from_a_slug_or_a_remote_url_and_nothing_else() -> None:
    gh = ScriptedGh(lambda args: "")

    assert GitHubRepo.from_slug("acme/harness", gh).slug == "acme/harness"
    assert GitHubRepo.from_slug("git@github.com:acme/harness.git", gh).slug == "acme/harness"
    with pytest.raises(ValueError, match="unsupported"):
        GitHubRepo.from_slug("https://example.com/owner/repo.git", gh)


def test_the_account_hands_out_the_issues_of_a_repository() -> None:
    gh = ScriptedGh(lambda args: "[[]]")

    assert GitHub(gh).issues("a/b").specs() == ()
    assert gh.calls[0][-1] == "repos/a/b/issues?state=open&labels=spec&per_page=100"
