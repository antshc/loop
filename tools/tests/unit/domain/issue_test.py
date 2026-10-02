#!/usr/bin/env python3
"""Unit tests for Issue.is_actionable().

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from modules.github.domain.issue import Issue
from modules.github.domain.issue_comment import IssueComment


class TestIssueActionability:
    """Feature: Issue Actionability"""

    def test_issue_with_no_labels_is_actionable(self):
        # Scenario: Issue with no labels is actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=[])
        assert issue.is_actionable is True

    def test_issue_with_unrelated_label_is_actionable(self):
        # Scenario: Issue with unrelated label is actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["enhancement"])
        assert issue.is_actionable is True

    def test_issue_with_blocked_label_is_actionable(self):
        # Scenario: Issue with blocked label is actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["blocked"])
        assert issue.is_actionable is True

    def test_issue_with_ready_and_blocked_labels_is_actionable(self):
        # Scenario: Issue with ready and blocked labels is actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["ready", "blocked"])
        assert issue.is_actionable is True

    def test_issue_with_hitl_label_is_not_actionable(self):
        # Scenario: Issue with hitl label is not actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["hitl"])
        assert issue.is_actionable is False

    def test_issue_with_spec_label_is_not_actionable(self):
        # Scenario: Issue with spec label is not actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["spec"])
        assert issue.is_actionable is False

    def test_issue_with_ready_and_spec_labels_is_not_actionable(self):
        # Scenario: Issue with ready and spec labels is not actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["ready", "spec"])
        assert issue.is_actionable is False

    def test_issue_with_blocked_and_hitl_labels_is_not_actionable(self):
        # Scenario: Issue with blocked and hitl labels is not actionable
        issue = Issue(number=12, title="Issue", body="", url="https://example.com/issues/12", labels=["blocked", "hitl"])
        assert issue.is_actionable is False

    def test_issue_comment_fields_are_preserved(self):
        # Scenario: Issue comment fields are preserved
        comment = IssueComment(id="IC_1", body="Need more context", created_at="2026-05-24T10:00:00Z")
        issue = Issue(
            number=12,
            title="Issue",
            body="",
            url="https://example.com/issues/12",
            labels=["ready"],
            comments=[comment],
        )
        assert issue.comments == [comment]
