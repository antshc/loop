"""Shared GitHub platform code for workflows: the issue and pull-request clients plus the Spec/Ticket tracker."""

from __future__ import annotations

from .github_repo import GhCli, GitHubRepo
from .pull_request_client import PullRequest, PullRequestClient, ReviewThread
from .repository import (
    PullRequests,
    Repository,
    RepositoryConfig,
    RepositoryPool,
    RepositoryPoolError,
    RepoTarget,
)
from .tracker import HITL_LABEL, Comment, IssueClient, Spec, Ticket, TicketsTracker
from .work_identifier import WorkIdentifier

__all__ = [
    "HITL_LABEL",
    "Comment",
    "GhCli",
    "GitHubRepo",
    "IssueClient",
    "PullRequest",
    "PullRequestClient",
    "PullRequests",
    "Repository",
    "RepoTarget",
    "RepositoryConfig",
    "RepositoryPool",
    "RepositoryPoolError",
    "ReviewThread",
    "Spec",
    "Ticket",
    "TicketsTracker",
    "WorkIdentifier",
]
