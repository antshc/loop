"""Shared GitHub platform code for workflows: the issue and pull-request clients plus the Spec/Ticket tracker."""

from __future__ import annotations

from .github_repo import GhCli, GitHubRepo
from .pull_request_client import PullRequest, PullRequestClient
from .repository import Repository, RepositoryId
from .tracker import HITL_LABEL, Comment, Spec, Ticket, TicketsTracker
from .agent_commit_subject import AgentCommitSubject

__all__ = [
    "HITL_LABEL",
    "Comment",
    "GhCli",
    "GitHubRepo",
    "PullRequest",
    "PullRequestClient",
    "Repository",
    "RepositoryId",
    "Spec",
    "Ticket",
    "TicketsTracker",
    "AgentCommitSubject",
]
