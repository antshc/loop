#!/usr/bin/env python3
"""Integration tests for the fetch_issues handler.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from modules.github.features.fetch_issues.handler import fetch_issues
from modules.github.infrastructure.gh_cli import GhCli
from modules.github.infrastructure.vcs_client import VCSClient

_OWNER = "owner"
_REPO = "repo"


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_raw_issue(
    number: int,
    labels: list[str],
    *,
    title: str | None = None,
    comments: list[dict] | None = None,
) -> dict:
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": f"Body for issue {number}",
        "url": f"https://github.com/owner/repo/issues/{number}",
        "labels": labels,
        "comments": comments or [
            {
                "id": f"IC_{number}",
                "body": f"Comment for issue {number}",
                "createdAt": "2026-05-24T10:00:00Z",
            }
        ],
    }



def setup_handler(issues_raw: list[dict]):
    """Return (owner, repo, vcs, gh) wired for a test with a mock GhCli."""
    mock_gh = MagicMock(spec=GhCli)
    mock_gh.fetch_issues_raw.return_value = issues_raw
    return _OWNER, _REPO, VCSClient(gh=mock_gh), mock_gh


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestFetchIssues:
    """Feature: Fetch Issues"""

    def test_returns_correctly_shaped_output_for_actionable_issues(self):
        # Scenario: Handler returns correctly shaped output for actionable issues
        issues_raw = [
            make_raw_issue(14, ["bug"]),
            make_raw_issue(
                15,
                ["blocked"],
                comments=[
                    {
                        "id": "IC_15",
                        "body": "Need a follow-up",
                        "createdAt": "2026-05-24T11:00:00Z",
                    }
                ],
            ),
        ]
        owner, repo, vcs, _ = setup_handler(issues_raw)

        result = fetch_issues(owner, repo, vcs=vcs)

        assert len(result) == 2
        issue = result[0]
        assert issue["number"] == 14
        assert issue["title"] == "Issue 14"
        assert issue["body"] == "Body for issue 14"
        assert issue["url"] == "https://github.com/owner/repo/issues/14"
        assert issue["labels"] == ["bug"]
        assert issue["comments"] == [
            {
                "id": "IC_14",
                "body": "Comment for issue 14",
                "created_at": "2026-05-24T10:00:00Z",
            }
        ]

    def test_returns_empty_list_when_no_actionable_issues(self):
        # Scenario: Handler returns empty list when no actionable issues exist
        issues_raw = [
            make_raw_issue(20, ["spec"]),
            make_raw_issue(21, ["hitl"]),
        ]
        owner, repo, vcs, _ = setup_handler(issues_raw)

        result = fetch_issues(owner, repo, vcs=vcs)

        assert result == []

    def test_includes_blocked_and_excludes_spec_and_hitl_issues(self):
        # Scenario: Handler includes blocked issues and excludes spec and hitl issues
        issues_raw = [
            make_raw_issue(30, ["blocked"]),
            make_raw_issue(31, ["spec"]),
            make_raw_issue(32, ["enhancement", "hitl"]),
            make_raw_issue(33, []),
        ]
        owner, repo, vcs, _ = setup_handler(issues_raw)

        result = fetch_issues(owner, repo, vcs=vcs)

        assert [issue["number"] for issue in result] == [30, 33]

    def test_spec_number_is_forwarded_to_gh_cli(self):
        # Scenario: Spec number is forwarded to GhCli
        issues_raw = [make_raw_issue(40, ["bug"])]
        owner, repo, vcs, gh = setup_handler(issues_raw)

        result = fetch_issues(owner, repo, spec_number=7, vcs=vcs)

        gh.fetch_issues_raw.assert_called_once_with("owner", "repo", 7)
        assert result == [
            {
                "number": 40,
                "title": "Issue 40",
                "body": "Body for issue 40",
                "url": "https://github.com/owner/repo/issues/40",
                "labels": ["bug"],
                "comments": [
                    {
                        "id": "IC_40",
                        "body": "Comment for issue 40",
                        "created_at": "2026-05-24T10:00:00Z",
                    }
                ],
            }
        ]

    def test_kind_selection_flows_through_shared_fetching(self):
        # Scenario: Kind selection flows through shared fetching
        owner, repo, vcs, gh = setup_handler([
            make_raw_issue(1, []), make_raw_issue(2, ["tests"]),
            make_raw_issue(3, ["tests", "hitl"]), make_raw_issue(4, ["spec"]),
        ])
        assert [i["number"] for i in fetch_issues(owner, repo, vcs=vcs)] == [1, 2]
        implementation = fetch_issues(owner, repo, kind="implementation", vcs=vcs)
        tests = fetch_issues(owner, repo, kind="tests", spec_number=7, vcs=vcs)
        assert [i["number"] for i in implementation] == [1]
        assert [i["number"] for i in tests] == [2]
        assert tests[0]["comments"][0]["body"] == "Comment for issue 2"
        gh.fetch_issues_raw.assert_called_with(owner, repo, 7)
