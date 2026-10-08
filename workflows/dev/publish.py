"""Publishes a feature branch and its draft pull request."""

from __future__ import annotations

from pathlib import Path

from loop import Branch, BranchService
from workflows.platforms.work_tracking import GitHubClient

from .planning import SpecRun


def _ensure_pull_request(github: GitHubClient, head: str, base: str, initiative: str, title: str) -> str:
    """One draft PR per feature branch; reruns reuse it. Returns its URL."""
    existing = github.find_pull_request(head)
    if existing is not None:
        return existing.url
    return github.create_draft_pull_request(head, base, f"{initiative}: {title}").url


def publish(branches: BranchService, run: SpecRun, pusher: Path) -> str | None:
    """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

    Returns the PR URL, or None when there was nothing to publish.
    """
    if not branches.push(Branch(pusher, run.feature_branch), Branch(pusher, run.spec.base_branch)):
        return None
    return _ensure_pull_request(run.target_github, run.feature_branch, run.spec.base_branch, run.spec.initiative, run.spec.bare_title)
