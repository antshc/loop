"""Repository paths, GitHub identity, and draft pull request publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .pull_request_client import PullRequestClient
from .tracker import Spec


class RepositoryId(NamedTuple):
    """GitHub owner and repository name, represented as an owner/repo slug."""

    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class Repository:
    """One repository's checkout, worktree directory, and GitHub identity."""

    checkout: Path
    worktrees: Path
    owner_name: RepositoryId


class PullRequests:
    """Publish draft pull requests through a repository-bound GitHub client."""

    def __init__(self, client: PullRequestClient) -> None:
        self._client = client

    def publish_draft(self, spec: Spec, feature_branch: str) -> str:
        """Reuse or create the Spec's draft PR from the feature branch into its base."""
        title = f"{spec.initiative}: {spec.bare_title}"
        return self._client.create_draft_pull_request(feature_branch, spec.base_branch, title).url
