#!/usr/bin/env python3
"""Unit tests for the fix_prs handler — structured log fields.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from afk.features.fix_prs.handler import fix_prs
from afk.infrastructure.ai_agent import AIAgent
from afk.shared.execution_log import ExecutionLog
from modules.github.domain.pull_request import PullRequest
from modules.github.domain.review_thread import ReviewThread
from modules.github.domain.comment import Comment
from modules.github.infrastructure.vcs_client import VCSClient

_USER = "alice"
_REPO = "owner/repo"
_LOG_DIR = Path("logs")
_PR_URL = "https://github.com/owner/repo/pull/1"
_PR_TITLE = "Fix race in queue consumer"


def _make_pr(url: str = _PR_URL) -> PullRequest:
    return PullRequest(owner="owner", repo="repo", number=1, url=url, title=_PR_TITLE)


def _make_thread(thread_id: str = "T1", body: str = "fix!: issue") -> ReviewThread:
    return ReviewThread(
        thread_id=thread_id,
        path="src/foo.py",
        lines="10-10",
        is_resolved=False,
        comments=[Comment(author="reviewer", body=body)],
    )


def _setup(
    prs: list,
    threads: list,
    exec_count: int = 0,
) -> tuple:
    vcs = MagicMock(spec=VCSClient)
    vcs.list_prs.return_value = prs
    vcs.fetch_review_threads.return_value = threads
    agent = MagicMock(spec=AIAgent)
    exec_log = MagicMock(spec=ExecutionLog)
    exec_log.get_count.return_value = exec_count
    return vcs, agent, exec_log


def _extra(record: logging.LogRecord) -> dict:
    return vars(record)


class TestFixPrsHandlerStructuredLogging:
    """Feature: Fix PRs Handler Structured Logging"""

    def test_service_run_started_includes_user_and_repo(self, caplog):
        # Scenario: Service run started log includes user and repo fields
        vcs, agent, exec_log = _setup([], [])

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "Service run started")
        assert _extra(record)["user"] == _USER
        assert _extra(record)["repo"] == _REPO

    def test_no_open_prs_log_includes_user_and_repo(self, caplog):
        # Scenario: No open PRs log includes user and repo fields
        vcs, agent, exec_log = _setup([], [])

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "No open PRs found for user")
        assert _extra(record)["user"] == _USER
        assert _extra(record)["repo"] == _REPO

    def test_found_open_prs_log_includes_count_and_user(self, caplog):
        # Scenario: Found open PRs log includes count and user fields
        vcs, agent, exec_log = _setup([_make_pr()], [_make_thread()])

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "Found open PRs")
        assert _extra(record)["count"] == 1
        assert _extra(record)["user"] == _USER

    def test_processing_pr_log_includes_pr_url(self, caplog):
        # Scenario: Processing PR log includes pr_url field
        vcs, agent, exec_log = _setup([_make_pr()], [_make_thread()])

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "Processing PR")
        assert _extra(record)["pr_url"] == _PR_URL

    def test_no_actionable_threads_log_includes_pr_url(self, caplog):
        # Scenario: No actionable threads log includes pr_url field
        vcs, agent, exec_log = _setup([_make_pr()], [])

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "No actionable threads, skipping")
        assert _extra(record)["pr_url"] == _PR_URL

    def test_exceeded_max_executions_log_includes_structured_fields(self, caplog):
        # Scenario: Exceeded max executions warning includes pr_url, count, max, unresolved_threads
        vcs, agent, exec_log = _setup([_make_pr()], [_make_thread()], exec_count=5)

        with caplog.at_level(logging.WARNING):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "PR exceeded max executions, skipping")
        extra = _extra(record)
        assert extra["pr_url"] == _PR_URL
        assert extra["count"] == 5
        assert extra["max"] == 5
        assert extra["unresolved_threads"] == 1

    def test_running_copilot_agent_log_includes_pr_url_threads_attempt(self, caplog):
        # Scenario: Running copilot agent log includes pr_url, threads, and attempt fields
        vcs, agent, exec_log = _setup([_make_pr()], [_make_thread()], exec_count=2)

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        record = next(r for r in caplog.records if r.getMessage() == "Running copilot agent")
        extra = _extra(record)
        assert extra["pr_url"] == _PR_URL
        assert extra["threads"] == 1
        assert extra["attempt"] == 3

    def test_completed_pr_processing_log_includes_pr_url_and_attempt(self, caplog):
        # Scenario: Completed PR processing log includes pr_url and attempt fields
        vcs, agent, exec_log = _setup([_make_pr()], [_make_thread()], exec_count=0)

        with caplog.at_level(logging.INFO):
            fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        exec_log.update.assert_called_once_with(_PR_URL, ["T1"], "owner", "repo", "pull_request", 1, _PR_TITLE)
        record = next(r for r in caplog.records if r.getMessage() == "Completed PR processing")
        extra = _extra(record)
        assert extra["pr_url"] == _PR_URL
        assert extra["attempt"] == 1

    @patch("afk.features.fix_prs.handler.ExecutionLog")
    def test_default_execution_log_uses_fix_prs_log_name(self, mock_execution_log_class):
        # Scenario: Default ExecutionLog is created with fix-prs log name
        vcs = MagicMock(spec=VCSClient)
        vcs.list_prs.return_value = []
        exec_log = MagicMock(spec=ExecutionLog)
        mock_execution_log_class.return_value = exec_log

        fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=MagicMock(spec=AIAgent))

        mock_execution_log_class.assert_called_once_with(_LOG_DIR, _REPO, "fix-prs")
