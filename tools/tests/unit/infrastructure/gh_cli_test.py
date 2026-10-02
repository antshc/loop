#!/usr/bin/env python3
"""Unit tests for GhCli spec and sub-issue GraphQL queries.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

from modules.github.infrastructure.gh_cli import GhCli, _SPEC_ISSUES_QUERY, _SUB_ISSUES_QUERY


def _node(number: int, state: str = "OPEN", labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "state": state,
        "labels": {"nodes": [{"name": name} for name in labels or []]},
        "comments": {"nodes": []},
    }


class TestGhCliSpecQueryConstruction:
    """Feature: GhCli Spec Query Construction"""

    @patch("modules.github.infrastructure.gh_cli.subprocess.run")
    def test_open_spec_issues_query_is_built_and_nodes_are_returned(self, mock_run):
        # Scenario: Open spec issues query is built and nodes are returned
        pages = [{"data": {"repository": {"issues": {"nodes": [_node(1, labels=["spec", "repo:owner/repo"])]}}}}]
        mock_run.return_value = Mock(stdout=json.dumps(pages))

        result = GhCli().list_specs_raw("owner", "repo")

        assert [item["number"] for item in result] == [1]
        assert result[0]["labels"] == ["spec", "repo:owner/repo"]
        assert result[0]["comments"] == []
        mock_run.assert_called_once_with(
            [
                "gh", "api", "graphql", "--paginate", "--slurp",
                "-f", f"query={_SPEC_ISSUES_QUERY}",
                "-f", "owner=owner",
                "-f", "repo=repo",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert 'labels: ["spec"]' in _SPEC_ISSUES_QUERY


class TestGhCliIssuePagination:
    """Feature: GhCli Issue Pagination"""

    @patch("modules.github.infrastructure.gh_cli.subprocess.run")
    def test_all_sub_issue_pages_are_collected_and_closed_ones_dropped(self, mock_run):
        # Scenario: All sub-issue pages are collected and closed ones dropped
        def page(*nodes):
            return {"data": {"repository": {"issue": {"subIssues": {"nodes": list(nodes)}}}}}
        pages = [page(_node(1, "CLOSED")), page(_node(2, labels=["tests"]))]
        mock_run.return_value = Mock(stdout=json.dumps(pages))

        result = GhCli().fetch_issues_raw("owner", "repo", 7)

        assert [item["number"] for item in result] == [2]
        assert result[0]["labels"] == ["tests"]
        command = mock_run.call_args.args[0]
        assert "--paginate" in command and "--slurp" in command
        assert command[command.index("-F") + 1] == "number=7"
        query = command[command.index("-f") + 1]
        assert query == f"query={_SUB_ISSUES_QUERY}"
        assert "after: $endCursor" in query
        assert "hasNextPage endCursor" in query

    @patch("modules.github.infrastructure.gh_cli.subprocess.run")
    def test_all_open_issue_pages_are_returned_without_spec_number(self, mock_run):
        # Scenario: All open issue pages are returned without a spec number
        def page(*nodes):
            return {"data": {"repository": {"issues": {"nodes": list(nodes)}}}}
        mock_run.return_value = Mock(stdout=json.dumps([page(_node(1)), page(_node(2))]))

        result = GhCli().fetch_issues_raw("owner", "repo")

        assert [item["number"] for item in result] == [1, 2]
        assert "-F" not in mock_run.call_args.args[0]
