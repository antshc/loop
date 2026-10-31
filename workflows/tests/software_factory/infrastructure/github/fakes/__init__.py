"""Fakes at the Workflow process boundaries, shared by every Workflow's tests."""

from __future__ import annotations

from .gh import PR_DIFF, FakeGhCli, ScriptedGh, issue
from .pull_requests import (
    FakePull,
    FakePullRequestHost,
    check_run,
    classic_protection,
    comment_node,
    commit_status,
    issue_comment,
    pull_request_rule,
    required_checks_rule,
    review,
    thread_node,
    workflow_run,
)

__all__ = [
    "PR_DIFF",
    "FakeGhCli",
    "FakePull",
    "FakePullRequestHost",
    "ScriptedGh",
    "check_run",
    "classic_protection",
    "comment_node",
    "commit_status",
    "issue",
    "issue_comment",
    "pull_request_rule",
    "required_checks_rule",
    "review",
    "thread_node",
    "workflow_run",
]
