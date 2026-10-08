"""Manually checked-out repositories: no git identity check, the user owns the clone and its path."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .gh_client import GitHubClient
from .pull_requests import PullRequests

RepoTarget = str
GithubFactory = Callable[[Path], GitHubClient]


@dataclass(frozen=True)
class RepositoryConfig:
    """A user-declared repository: its checkout path and `owner/name`; one must carry `is_harness`."""

    path: Path
    owner_repo: RepoTarget
    is_harness: bool = False


@dataclass(frozen=True)
class Repository:
    """A `RepositoryConfig` resolved to its ready-to-use `PullRequests`."""

    path: Path
    owner_repo: RepoTarget
    is_harness: bool
    pull_requests: PullRequests


class RepositoryPoolError(Exception):
    """Raised when `RepositoryPool` is configured with zero or more than one harness repository."""


class RepositoryPool:
    """Every manually checked-out Repository, keyed by `owner/name`; built once from `RepositoryConfig`."""

    def __init__(self, configs: Sequence[RepositoryConfig], github_factory: GithubFactory) -> None:
        repositories = [
            Repository(
                path=config.path,
                owner_repo=config.owner_repo,
                is_harness=config.is_harness,
                pull_requests=PullRequests(github_factory(config.path)),
            )
            for config in configs
        ]
        self._by_target = {repository.owner_repo.casefold(): repository for repository in repositories}
        harnesses = [repository for repository in repositories if repository.is_harness]
        if len(harnesses) != 1:
            raise RepositoryPoolError(f"expected exactly one is_harness repository, found {len(harnesses)}")
        self._harness = harnesses[0]

    @property
    def harness(self) -> Repository:
        return self._harness

    def get(self, target: RepoTarget) -> Repository | None:
        """The Repository configured for `target`, or None when it has no entry in the pool."""
        return self._by_target.get(target.casefold())
