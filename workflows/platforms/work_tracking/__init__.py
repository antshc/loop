"""Shared GitHub platform code for workflows: the `gh` client plus the Spec/Ticket tracker."""

from __future__ import annotations

from .gh_client import GhCli, GitHubClient, PullRequest, ReviewThread
from .repository import (
    PullRequests,
    Repository,
    RepositoryConfig,
    RepositoryPool,
    RepositoryPoolError,
    RepoTarget,
)
from .tracker import HITL_LABEL, Comment, Spec, Ticket, TicketsTracker
from .work_identifier import WorkIdentifier

__all__ = [
    "HITL_LABEL",
    "Comment",
    "GhCli",
    "GitHubClient",
    "PullRequest",
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
