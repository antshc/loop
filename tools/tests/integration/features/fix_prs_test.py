#!/usr/bin/env python3
"""Integration tests for the fix_prs handler.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from pathlib import Path
from unittest.mock import MagicMock

from afk.infrastructure.ai_agent import AIAgent
from modules.github.infrastructure.gh_cli import GhCli
from modules.github.infrastructure.vcs_client import VCSClient
from afk.shared.execution_log import ExecutionLog
from afk.features.fix_prs.handler import fix_prs

_USER = "dev"
_REPO = "owner/repo"
_LOG_DIR = Path("/tmp/ralph-test-logs")

_PR1_URL = "https://github.com/owner/repo/pull/1"
_PR2_URL = "https://github.com/owner/repo/pull/2"
_PR3_URL = "https://github.com/owner/repo/pull/3"
_PR1_TITLE = "Fix parser edge case"
_PR2_TITLE = "Extract retry helper"
_PR3_TITLE = "Refactor auth flow"


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


def make_pr_dict(url: str, title: str) -> dict:
    return {"url": url, "title": title}


def setup_handler(prs: list[tuple[str, str]], threads_by_pr_number: dict[int, list[dict]], exec_count_by_url: dict[str, int] | None = None):
    """Return (mock_gh, vcs, mock_agent, mock_exec_log) wired for a multi-PR test."""
    mock_gh = MagicMock(spec=GhCli)
    mock_gh.pr_list.return_value = [make_pr_dict(url, title) for (url, title) in prs]
    mock_gh.fetch_threads_raw.side_effect = lambda owner, repo, number: threads_by_pr_number.get(number, [])
    vcs = VCSClient(gh=mock_gh)

    mock_agent = MagicMock(spec=AIAgent)

    mock_exec_log = MagicMock(spec=ExecutionLog)
    if exec_count_by_url:
        mock_exec_log.get_count.side_effect = lambda url: exec_count_by_url.get(url, 0)
    else:
        mock_exec_log.get_count.return_value = 0

    return mock_gh, vcs, mock_agent, mock_exec_log


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestReviewPullRequests:
    """Feature: Fix Multiple PRs (fix_prs handler)"""

    def test_multiple_prs_with_actionable_threads_are_all_processed(self):
        # Scenario: Multiple PRs with actionable threads are all processed
        threads_by_pr = {
            1: [make_raw_thread("T1", "fix!: issue in PR1")],
            2: [make_raw_thread("T2", "suggest!: extract in PR2")],
        }
        mock_gh, vcs, mock_agent, mock_exec_log = setup_handler([(_PR1_URL, _PR1_TITLE), (_PR2_URL, _PR2_TITLE)], threads_by_pr)

        fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=mock_agent, exec_log=mock_exec_log)

        assert mock_agent.run.call_count == 2
        mock_exec_log.update.assert_any_call(_PR1_URL, ["T1"], "owner", "repo", "pull_request", 1, _PR1_TITLE)
        mock_exec_log.update.assert_any_call(_PR2_URL, ["T2"], "owner", "repo", "pull_request", 2, _PR2_TITLE)

    def test_no_open_prs_early_exit(self):
        # Scenario: No open PRs found — early exit
        mock_gh, vcs, mock_agent, mock_exec_log = setup_handler([], {})

        fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=mock_agent, exec_log=mock_exec_log)

        mock_agent.run.assert_not_called()

    def test_mix_of_actionable_and_non_actionable_prs(self):
        # Scenario: Mix of actionable and non-actionable PRs
        threads_by_pr = {
            1: [make_raw_thread("T1", "fix!: issue")],
            2: [make_raw_thread("T2", "nit: style")],
            3: [make_raw_thread("T3", "suggest!: extract")],
        }
        mock_gh, vcs, mock_agent, mock_exec_log = setup_handler(
            [(_PR1_URL, _PR1_TITLE), (_PR2_URL, _PR2_TITLE), (_PR3_URL, _PR3_TITLE)],
            threads_by_pr,
        )

        fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=mock_agent, exec_log=mock_exec_log)

        assert mock_agent.run.call_count == 2
        mock_exec_log.update.assert_any_call(_PR1_URL, ["T1"], "owner", "repo", "pull_request", 1, _PR1_TITLE)
        mock_exec_log.update.assert_any_call(_PR3_URL, ["T3"], "owner", "repo", "pull_request", 3, _PR3_TITLE)

    def test_pr_at_max_executions_skipped_while_others_processed(self):
        # Scenario: PR at max executions is skipped while others are processed
        threads_by_pr = {
            1: [make_raw_thread("T1", "fix!: issue in PR1")],
            2: [make_raw_thread("T2", "fix!: issue in PR2")],
        }
        exec_counts = {_PR1_URL: 5, _PR2_URL: 0}
        mock_gh, vcs, mock_agent, mock_exec_log = setup_handler([(_PR1_URL, _PR1_TITLE), (_PR2_URL, _PR2_TITLE)], threads_by_pr, exec_counts)

        fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=mock_agent, exec_log=mock_exec_log)

        assert mock_agent.run.call_count == 1
        mock_exec_log.update.assert_called_once_with(_PR2_URL, ["T2"], "owner", "repo", "pull_request", 2, _PR2_TITLE)

    def test_pr_with_no_actionable_threads_and_prior_count_resets_log(self):
        # Scenario: PR with no actionable threads and prior count resets execution log
        threads_by_pr = {1: [make_raw_thread("T1", "nit: style")]}
        mock_gh, vcs, mock_agent, mock_exec_log = setup_handler([(_PR1_URL, _PR1_TITLE)], threads_by_pr, {_PR1_URL: 3})

        fix_prs(_USER, _REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=mock_agent, exec_log=mock_exec_log)

        mock_agent.run.assert_not_called()
        mock_exec_log.reset.assert_called_once_with(_PR1_URL)
