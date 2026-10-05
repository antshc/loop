#!/usr/bin/env python3
"""Integration tests for the fetch_threads handler.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from unittest.mock import MagicMock

from modules.github.infrastructure.gh_cli import GhCli
from modules.github.infrastructure.vcs_client import VCSClient
from modules.github.features.fetch_threads.handler import fetch_threads

_PR_URL = "https://github.com/owner/repo/pull/1"


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_raw_thread(id: str, body: str, resolved: bool = False, line: int = 10) -> dict:
    return {
        "id": id,
        "isResolved": resolved,
        "path": "src/foo.py",
        "startLine": line,
        "line": line,
        "comments": [{"author": {"login": "reviewer"}, "body": body}],
    }


def setup_handler(threads_raw: list[dict]):
    """Return (pr_url, vcs) wired for a test with a mock GhCli."""
    mock_gh = MagicMock(spec=GhCli)
    mock_gh.fetch_threads_raw.return_value = threads_raw
    return _PR_URL, VCSClient(gh=mock_gh)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestFetchThreads:
    """Feature: Fetch Threads"""

    def test_returns_correctly_shaped_output_for_actionable_threads(self):
        # Scenario: Handler returns correctly shaped output for actionable threads
        threads_raw = [
            make_raw_thread("T1", "fix!: broken null check"),
            make_raw_thread("T2", "suggest!: extract method"),
        ]
        pr_url, vcs = setup_handler(threads_raw)

        result = fetch_threads(pr_url, vcs=vcs)

        assert len(result) == 2
        t1 = result[0]
        assert t1["thread_id"] == "T1"
        assert t1["prefix"] == "fix!"
        assert t1["path"] == "src/foo.py"
        assert t1["lines"] == "10-10"
        assert t1["actionable_comment"] == "fix!: broken null check"
        assert t1["comments"] == [{"author": "reviewer", "body": "fix!: broken null check"}]

    def test_returns_empty_list_when_no_actionable_threads(self):
        # Scenario: Handler returns empty list when no actionable threads exist
        threads_raw = [
            make_raw_thread("T1", "nit: minor style"),
            make_raw_thread("T2", "good: nice approach"),
        ]
        pr_url, vcs = setup_handler(threads_raw)

        result = fetch_threads(pr_url, vcs=vcs)

        assert result == []

    def test_excludes_resolved_and_non_actionable_threads(self):
        # Scenario: Handler excludes resolved and non-actionable threads
        threads_raw = [
            make_raw_thread("T1", "fix!: broken null check"),
            make_raw_thread("T2", "fix!: another issue", resolved=True),
            make_raw_thread("T3", "nit: minor style"),
        ]
        pr_url, vcs = setup_handler(threads_raw)

        result = fetch_threads(pr_url, vcs=vcs)

        assert len(result) == 1
        assert result[0]["thread_id"] == "T1"
