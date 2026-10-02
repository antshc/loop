#!/usr/bin/env python3
"""Unit tests for VCSClient mapping helpers.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from modules.github.infrastructure.tests.fake_gh_cli import FakeGhCli
from modules.github.infrastructure.vcs_client import VCSClient


class TestVCSClientPrMapping:
    """Feature: VCS Client PR Mapping"""

    def test_pr_list_nodes_are_mapped_to_pull_request_domain_entities(self):
        # Scenario: Raw PR nodes are mapped to PullRequest domain entities
        client = VCSClient(
            gh=FakeGhCli(
                pr_list_output=[
                    {
                        "url": "https://github.com/owner/repo/pull/42",
                        "title": "Fix cache invalidation bug",
                    }
                ]
            )
        )

        result = client.list_prs("alice", "owner/repo")

        assert len(result) == 1
        assert result[0].owner == "owner"
        assert result[0].repo == "repo"
        assert result[0].number == 42
        assert result[0].url == "https://github.com/owner/repo/pull/42"
        assert result[0].title == "Fix cache invalidation bug"

    def test_missing_pr_title_maps_to_empty_string(self):
        # Scenario: Missing PR title maps to empty string
        client = VCSClient(
            gh=FakeGhCli(
                pr_list_output=[
                    {
                        "url": "https://github.com/owner/repo/pull/7",
                    }
                ]
            )
        )

        result = client.list_prs("alice", "owner/repo")

        assert len(result) == 1
        assert result[0].title == ""


class TestVCSClientThreadMapping:
    """Feature: VCS Client Thread Mapping"""

    def test_raw_thread_node_is_mapped_to_review_thread_domain_entity(self):
        # Scenario: Raw thread node is mapped to ReviewThread domain entity
        raw = {
            "id": "T1",
            "path": "src/foo.ts",
            "startLine": 10,
            "line": 15,
            "isResolved": False,
            "comments": [{"author": {"login": "reviewer"}, "body": "fix!: issue"}],
        }
        result = VCSClient()._thread_from_raw(raw)
        assert result.thread_id == "T1"
        assert result.path == "src/foo.ts"
        assert result.lines == "10-15"
        assert result.is_resolved is False
        assert result.comments[0].author == "reviewer"
        assert result.comments[0].body == "fix!: issue"

    def test_missing_start_line_falls_back_to_line_for_both_start_and_end(self):
        # Scenario: Missing startLine falls back to line for both start and end
        raw = {
            "id": "T2",
            "path": "a.py",
            "startLine": None,
            "line": 5,
            "isResolved": False,
            "comments": [],
        }
        result = VCSClient()._thread_from_raw(raw)
        assert result.lines == "5-5"


class TestVCSClientIssueMapping:
    """Feature: VCS Client Issue Mapping"""

    def test_raw_issue_nodes_are_mapped_to_issue_domain_entities(self):
        # Scenario: Raw issue nodes are mapped to Issue domain entities
        client = VCSClient(
            gh=FakeGhCli(
                issues_raw=[
                    {
                        "number": 13,
                        "title": "Add fetch issues",
                        "body": "Need issue + comment mapping",
                        "url": "https://github.com/owner/repo/issues/13",
                        "labels": ["ready", "bug"],
                        "comments": [
                            {
                                "id": "IC_1",
                                "body": "Need more context",
                                "createdAt": "2026-05-24T10:00:00Z",
                            }
                        ],
                    }
                ]
            )
        )

        result = client.fetch_issues("owner", "repo")

        assert len(result) == 1
        assert result[0].number == 13
        assert result[0].title == "Add fetch issues"
        assert result[0].body == "Need issue + comment mapping"
        assert result[0].url == "https://github.com/owner/repo/issues/13"
        assert result[0].labels == ["ready", "bug"]
        assert result[0].comments[0].id == "IC_1"
        assert result[0].comments[0].body == "Need more context"
        assert result[0].comments[0].created_at == "2026-05-24T10:00:00Z"

    def test_missing_issue_labels_and_comments_map_to_empty_lists(self):
        # Scenario: Missing issue labels and comments map to empty lists
        client = VCSClient(
            gh=FakeGhCli(
                issues_raw=[
                    {
                        "number": 21,
                        "title": "No metadata",
                        "body": "",
                        "url": "https://github.com/owner/repo/issues/21",
                    }
                ]
            )
        )

        result = client.fetch_issues("owner", "repo")

        assert len(result) == 1
        assert result[0].labels == []
        assert result[0].comments == []


class TestVCSClientSpecMapping:
    """Feature: VCS Client Spec Mapping"""

    def test_raw_spec_nodes_are_mapped_to_issue_domain_entities(self):
        # Scenario: Raw spec nodes are mapped to Issue domain entities
        client = VCSClient(
            gh=FakeGhCli(
                specs_raw=[
                    {
                        "number": 1,
                        "title": "PROJ-1: First delivery slice",
                        "body": "```metadata\ninitiative_id: PROJ-1\ntarget_branch: main\n```",
                        "url": "https://github.com/owner/repo/issues/1",
                        "labels": ["spec", "repo:owner/repo"],
                    }
                ]
            )
        )

        result = client.list_specs("owner", "repo")

        assert len(result) == 1
        assert result[0].number == 1
        assert result[0].title == "PROJ-1: First delivery slice"
        assert result[0].body.startswith("```metadata")
        assert result[0].labels == ["spec", "repo:owner/repo"]

    def test_no_spec_nodes_map_to_empty_list(self):
        # Scenario: No spec nodes map to an empty list
        client = VCSClient(gh=FakeGhCli())

        assert client.list_specs("owner", "repo") == []
