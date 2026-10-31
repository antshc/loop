from __future__ import annotations

import json

import pytest
from loop.shared.github.account import GitHub
from loop.shared.github.pull_requests import PullRequests
from loop.shared.github.repo import GitHubRepo, denied
from loop.shared.process import CommandError

from tests.shared.fakes import FakeGhCli, FakePullRequestHost, ScriptedGh


def _pulls(gh: ScriptedGh) -> PullRequests:
    return PullRequests(GitHubRepo("a", "b", gh=gh))


def test_a_draft_pull_request_is_reused_for_its_head_branch() -> None:
    existing = [
        {"number": 5, "title": "t", "url": "u", "headRefName": "feat", "body": "old"}
    ]
    gh = ScriptedGh(
        lambda args: json.dumps(existing) if args[:2] == ("pr", "list") else ""
    )

    pr = _pulls(gh).create_draft_pull_request("feat", "main", "t", "new")

    assert (pr.number, pr.body) == (5, "new")
    assert [call[:2] for call in gh.calls] == [("pr", "list"), ("pr", "edit")]


def test_a_missing_draft_pull_request_is_created_as_a_draft() -> None:
    gh = ScriptedGh(
        lambda args: (
            "[]" if args[:2] == ("pr", "list") else "https://github.com/a/b/pull/7\n"
        )
    )

    pr = _pulls(gh).create_draft_pull_request("feat", "main", "Title")

    assert (pr.number, pr.url, pr.branch) == (
        7,
        "https://github.com/a/b/pull/7",
        "feat",
    )
    assert gh.calls[-1][-1] == "--draft" and ("--repo", "a/b") == gh.calls[-1][2:4]


def test_every_reader_rejects_a_page_that_is_not_a_list_the_same_way() -> None:
    repo = GitHubRepo(
        "a", "b", gh=ScriptedGh(lambda args: json.dumps([{"unexpected": 1}]))
    )

    with pytest.raises(ValueError, match="expected a list of pages"):
        repo.pages("repos/a/b/pulls")
    with pytest.raises(ValueError, match="expected a list of pages"):
        PullRequests(repo).open_by("me")
    with pytest.raises(ValueError, match="expected a list of pages"):
        repo.pages("repos/a/b/actions/runs", items="workflow_runs")


def test_pages_collects_the_named_list_of_each_object_page() -> None:
    pages = [{"check_runs": [{"id": 1}]}, {"check_runs": [{"id": 2}]}]
    repo = GitHubRepo("a", "b", gh=ScriptedGh(lambda args: json.dumps(pages)))

    assert repo.pages("repos/a/b/commits/x/check-runs", items="check_runs") == [
        {"id": 1},
        {"id": 2},
    ]


@pytest.mark.parametrize(
    ("output", "expected"),
    [("HTTP 403", True), ("gh: Not Found (HTTP 404)", True), ("HTTP 500", False)],
)
def test_gh_refusing_a_request_is_told_from_the_request_failing(
    output: str, expected: bool
) -> None:
    assert denied(CommandError("gh api x", 1, output)) is expected


def test_the_account_reaches_repositories_by_slug_and_reports_its_login() -> None:
    github = GitHub(ScriptedGh(lambda args: '{"login": "me"}'))

    assert github.login() == "me"
    assert github.repository("https://github.com/o/r.git").slug == "o/r"
    with pytest.raises(ValueError):
        github.repository("not-a-slug")


def test_the_account_reads_one_pull_request_of_a_repository() -> None:
    host = FakePullRequestHost(slug="o/r")
    host.add(7)
    gh = FakeGhCli(pulls=host)

    pull = GitHub(gh).pull_requests("o/r").pull_request(7)

    assert (pull.number, str(pull.ref)) == (7, "o/r#7")
    assert gh.calls[-1][:2] == ("api", "repos/o/r/pulls/7")


def test_a_repository_is_read_from_a_slug_or_a_remote_url_and_nothing_else() -> None:
    gh = ScriptedGh(lambda args: "")

    assert GitHubRepo.from_slug("acme/harness", gh).slug == "acme/harness"
    assert (
        GitHubRepo.from_slug("git@github.com:acme/harness.git", gh).slug
        == "acme/harness"
    )
    with pytest.raises(ValueError, match="unsupported"):
        GitHubRepo.from_slug("https://example.com/owner/repo.git", gh)


def test_the_account_hands_out_the_issues_of_a_repository() -> None:
    gh = ScriptedGh(lambda args: "[[]]")

    assert GitHub(gh).issues("a/b").specs() == ()
    assert gh.calls[0][-1] == "repos/a/b/issues?state=open&labels=spec&per_page=100"
