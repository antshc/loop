#!/usr/bin/env python3
"""Fetch Issues use case: fetches actionable issues for a repository."""

from __future__ import annotations

from ...domain.issue import Issue
from ...domain.services.issue_filter import IssueFilter
from ...infrastructure.vcs_client import VCSClient


def fetch_issues(
    owner: str,
    repo: str,
    *,
    spec_number: int | None = None,
    kind: str = "all",
    vcs: VCSClient | None = None,
) -> list[dict]:
    """Fetch and return actionable issues for a repository as serialisable dicts."""
    vcs = vcs or VCSClient()
    issue_filter = IssueFilter()

    fetched_issues = vcs.fetch_issues(owner, repo, spec_number)
    actionable_issues = issue_filter.get_actionable_issues(fetched_issues, kind=kind)

    return [_issue_to_dict(issue) for issue in actionable_issues]


def _issue_to_dict(issue: Issue) -> dict:
    return {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "url": issue.url,
        "labels": issue.labels,
        "comments": [
            {
                "id": comment.id,
                "body": comment.body,
                "created_at": comment.created_at,
            }
            for comment in issue.comments
        ],
    }
