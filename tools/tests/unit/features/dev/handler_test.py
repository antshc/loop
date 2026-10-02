#!/usr/bin/env python3
"""Unit tests for the dev spec handler.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from afk.features.dev.handler import dev
from afk.infrastructure.ai_agent import AIAgent
from afk.shared.execution_log import ExecutionLog
from modules.github.domain.issue import Issue
from modules.github.infrastructure.vcs_client import VCSClient

_LOG_DIR = Path("logs")
_GITHUB_REPO = "owner/repo"


def _spec(number: int = 3, title: str = "PROJ-3: Delivery slice") -> Issue:
    return Issue(
        number=number,
        title=title,
        body="```metadata\ninitiative_id: PROJ-3\ntarget_branch: main\n```",
        url=f"https://github.com/owner/repo/issues/{number}",
        labels=["spec", "repo:owner/repo"],
    )


def _ticket(number: int, labels: list[str]) -> Issue:
    return Issue(number=number, title="Ticket", body="", url=f"https://github.com/owner/repo/issues/{number}", labels=labels)


class TestDevSpecLoop:
    """Feature: Dev Spec Loop"""

    def test_no_open_specs_early_exit(self, caplog):
        # Scenario: No open specs found — early exit
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = []
        agent = MagicMock(spec=AIAgent)
        exec_log = MagicMock(spec=ExecutionLog)

        with caplog.at_level(logging.INFO):
            dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        vcs.list_specs.assert_called_once_with("owner", "repo")
        vcs.fetch_issues.assert_not_called()
        agent.run.assert_not_called()
        assert "No open specs found" in caplog.text

    def test_spec_with_no_actionable_issues_and_prior_count_resets_log(self, caplog):
        # Scenario: Spec with no actionable issues and prior count resets execution log
        spec = _spec()
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [spec]
        vcs.fetch_issues.return_value = [_ticket(14, ["hitl"])]
        agent = MagicMock(spec=AIAgent)
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 3

        with caplog.at_level(logging.INFO):
            dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        vcs.fetch_issues.assert_called_once_with("owner", "repo", spec.number)
        exec_log.get_count.assert_called_once_with(spec.url)
        exec_log.reset.assert_called_once_with(spec.url)
        exec_log.update.assert_not_called()
        agent.run.assert_not_called()
        assert "No actionable issues, skipping" in caplog.text
        assert "Reset execution count (all issues resolved)" in caplog.text

    def test_spec_with_no_actionable_issues_and_zero_count_does_not_reset_log(self, caplog):
        # Scenario: Spec with no actionable issues and zero count does not reset execution log
        spec = _spec()
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [spec]
        vcs.fetch_issues.return_value = [_ticket(14, ["hitl"])]
        agent = MagicMock(spec=AIAgent)
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 0

        with caplog.at_level(logging.INFO):
            dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        vcs.fetch_issues.assert_called_once_with("owner", "repo", spec.number)
        exec_log.get_count.assert_called_once_with(spec.url)
        exec_log.reset.assert_not_called()
        exec_log.update.assert_not_called()
        agent.run.assert_not_called()
        assert "No actionable issues, skipping" in caplog.text

    def test_spec_at_max_executions_is_skipped(self, caplog):
        # Scenario: Spec at max executions is skipped
        spec = _spec()
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [spec]
        vcs.fetch_issues.return_value = [_ticket(14, ["ready"])]
        agent = MagicMock(spec=AIAgent)
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 5

        with caplog.at_level(logging.WARNING):
            dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        exec_log.get_count.assert_called_once_with(spec.url)
        exec_log.update.assert_not_called()
        agent.run.assert_not_called()
        assert "Spec exceeded max executions, skipping" in caplog.text

    @patch("afk.features.dev.handler.AIAgent")
    def test_actionable_spec_invokes_agent_and_updates_execution_log(self, mock_agent_class):
        # Scenario: Actionable spec invokes agent and updates execution log
        spec = _spec()
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [spec]
        vcs.fetch_issues.return_value = [_ticket(14, ["ready"])]
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 0
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, exec_log=exec_log)

        vcs.list_specs.assert_called_once_with("owner", "repo")
        vcs.fetch_issues.assert_called_once_with("owner", "repo", spec.number)
        exec_log.get_count.assert_called_once_with(spec.url)
        mock_agent_class.assert_called_once_with(alias="yolo", prompt="/ralph:dev 3")
        mock_agent.run.assert_called_once_with()
        exec_log.update.assert_called_once_with(spec.url, [14], "owner", "repo", "spec", 3, spec.title)

    @patch("afk.features.dev.handler.AIAgent")
    def test_spec_whose_run_ends_early_does_not_stop_iteration(self, mock_agent_class):
        # Scenario: Continue with the next spec when the current spec's run ends early
        first_spec = _spec(3, "PROJ-3: First")
        second_spec = _spec(4, "PROJ-4: Second")
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [first_spec, second_spec]
        vcs.fetch_issues.side_effect = [[_ticket(14, ["ready"])], [_ticket(15, ["ready"])]]
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 0
        # AIAgent.run() never raises when the ralph `dev` skill stops early (e.g. before any
        # worktree), so the handler must still reach the next spec.
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, exec_log=exec_log)

        assert mock_agent.run.call_count == 2
        exec_log.update.assert_any_call(first_spec.url, [14], "owner", "repo", "spec", 3, first_spec.title)
        exec_log.update.assert_any_call(second_spec.url, [15], "owner", "repo", "spec", 4, second_spec.title)

    @patch("afk.features.dev.handler.ExecutionLog")
    def test_default_execution_log_uses_dev_log_name(self, mock_execution_log_class):
        # Scenario: Default ExecutionLog is created with dev log name
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [_spec()]
        vcs.fetch_issues.return_value = []
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 0
        mock_execution_log_class.return_value = exec_log

        dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=MagicMock(spec=AIAgent))

        mock_execution_log_class.assert_called_once_with(_LOG_DIR, _GITHUB_REPO, "dev")

    def test_tests_only_spec_still_starts_ralph(self):
        # Scenario: Tests-only spec still starts Ralph
        vcs = MagicMock(spec=VCSClient)
        vcs.list_specs.return_value = [_spec()]
        vcs.fetch_issues.return_value = [_ticket(20, ["tests"])]
        agent = MagicMock(spec=AIAgent)
        exec_log = MagicMock(spec=ExecutionLog)
        exec_log.get_count.return_value = 0

        dev(_GITHUB_REPO, _LOG_DIR, max_executions=5, vcs=vcs, agent=agent, exec_log=exec_log)

        agent.run.assert_called_once_with()
