"""Shared GitHub platform code for workflows: the issue and pull-request clients plus the Spec/Ticket tracker."""

from __future__ import annotations

from .github_repo import GhCli, GitHubRepo
from .pull_request_client import PullRequest, PullRequestClient, ReviewThread
from .repository import PullRequests, Repository, RepositoryId
from .tracker import HITL_LABEL, Comment, Spec, Ticket, TicketsTracker
from .work_identifier import WorkIdentifier

__all__ = [
    "HITL_LABEL",
    "Comment",
    "GhCli",
    "GitHubRepo",
    "PullRequest",
    "PullRequestClient",
    "PullRequests",
    "Repository",
    "RepositoryId",
    "ReviewThread",
    "Spec",
    "Ticket",
    "TicketsTracker",
    "WorkIdentifier",
]
