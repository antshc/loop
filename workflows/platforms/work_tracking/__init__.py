"""Shared GitHub platform code for workflows: the `gh` client plus the Spec/Ticket tracker."""

from __future__ import annotations

from .gh_client import GhCli, GitHubClient, PullRequest, ReviewThread
from .pull_requests import PullRequests
from .repository import Repository, RepoTarget, RepositoryConfig, RepositoryPool, RepositoryPoolError
from .tracker import HITL_LABEL, Comment, Spec, Ticket, TicketsTracker

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
]
