"""Publishes a feature branch and its draft pull request."""

from __future__ import annotations

from pathlib import Path

from loop import Branch, BranchService

from .planning import SpecRun


def publish(branches: BranchService, run: SpecRun, pusher: Path) -> str | None:
    """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

    Returns the PR URL, or None when there was nothing to publish.
    """
    if not branches.push(Branch(pusher, run.spec.feature_branch), Branch(pusher, run.spec.base_branch)):
        return None
    spec = run.spec
    pull_request = run.target_github.create_draft_pull_request(
        run.spec.feature_branch, spec.base_branch, f"{spec.initiative}: {spec.bare_title}"
    )
    return pull_request.url
