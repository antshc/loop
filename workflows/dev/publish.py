"""Publishes a feature branch and its draft pull request."""

from __future__ import annotations

from pathlib import Path

from loop import GitClient
from workflows.platforms.work_tracking import GitHubClient

from .planning import SpecRun


def _ensure_pull_request(github: GitHubClient, head: str, base: str, initiative: str, title: str) -> str:
    """One draft PR per feature branch; reruns reuse it. Returns its URL."""
    existing = github.find_pull_request(head)
    if existing is not None:
        return existing.url
    return github.create_draft_pull_request(head, base, f"{initiative}: {title}").url


def publish(git: GitClient, run: SpecRun, pusher: Path) -> str | None:
    """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

    Returns the PR URL, or None when there was nothing to publish.
    """
    if not git.branch_ahead_of_remote(run.checkout, run.feature_branch, f"origin/{run.base_branch}"):
        return None
    git.push(pusher, run.feature_branch)
    return _ensure_pull_request(run.target_github, run.feature_branch, run.base_branch, run.initiative, run.bare_title)
