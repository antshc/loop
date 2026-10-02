#!/usr/bin/env python3
"""Unit tests for fetch_threads handler.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from unittest.mock import MagicMock

from modules.github.domain.comment import Comment
from modules.github.domain.review_thread import ReviewThread
from modules.github.features.fetch_threads.handler import fetch_threads
from modules.github.infrastructure.vcs_client import VCSClient


def _make_thread(thread_id: str, body: str, resolved: bool = False) -> ReviewThread:
    return ReviewThread(
        thread_id=thread_id,
        path="src/foo.py",
        lines="10-10",
        is_resolved=resolved,
        comments=[Comment(author="reviewer", body=body)],
    )


class TestFetchThreads:
    """Feature: Fetch Threads"""

    def test_handler_returns_correctly_shaped_output_for_actionable_threads(self):
        # Scenario: Handler returns correctly shaped output for actionable threads
        mock_vcs = MagicMock(spec=VCSClient)
        mock_vcs.fetch_review_threads.return_value = [
            _make_thread("T1", "fix!: broken null check"),
            _make_thread("T2", "suggest!: extract method"),
        ]

        result = fetch_threads("https://github.com/owner/repo/pull/1", vcs=mock_vcs)

        assert len(result) == 2
        t1 = result[0]
        assert t1["thread_id"] == "T1"
        assert t1["prefix"] == "fix!"
        assert t1["path"] == "src/foo.py"
        assert t1["lines"] == "10-10"
        assert t1["actionable_comment"] == "fix!: broken null check"
        assert t1["comments"] == [{"author": "reviewer", "body": "fix!: broken null check"}]

    def test_handler_returns_empty_list_when_no_actionable_threads(self):
        # Scenario: Handler returns empty list when no actionable threads exist
        mock_vcs = MagicMock(spec=VCSClient)
        mock_vcs.fetch_review_threads.return_value = [
            _make_thread("T1", "nit: minor style"),
            _make_thread("T2", "good: nice approach"),
        ]

        result = fetch_threads("https://github.com/owner/repo/pull/2", vcs=mock_vcs)

        assert result == []

    def test_handler_excludes_resolved_and_non_actionable_threads(self):
        # Scenario: Handler excludes resolved and non-actionable threads
        mock_vcs = MagicMock(spec=VCSClient)
        mock_vcs.fetch_review_threads.return_value = [
            _make_thread("T1", "fix!: broken null check"),
            _make_thread("T2", "fix!: another issue", resolved=True),
            _make_thread("T3", "nit: minor style"),
        ]

        result = fetch_threads("https://github.com/owner/repo/pull/3", vcs=mock_vcs)

        assert len(result) == 1
        assert result[0]["thread_id"] == "T1"
