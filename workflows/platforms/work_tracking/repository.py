"""Manually checked-out repositories: no git identity check, the user owns the clone and its path."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loop import RepositoryData

from .gh_client import GitHubClient
from .tracker import Spec

RepoTarget = str
GithubFactory = Callable[[Path], GitHubClient]


class PullRequests:
    """The target repo's draft pull requests; hides which repo hosts them and how titles are formed."""

    def __init__(self, github: GitHubClient) -> None:
        self._github = github

    def publish_draft(self, spec: Spec, feature_branch: str) -> str:
        """URL of the Spec's draft PR from `feature_branch` into its base branch, created when none is open.

        Titles it `<initiative>: <bare title>`; an existing PR is reused unchanged.
        """
        title = f"{spec.initiative}: {spec.bare_title}"
        return self._github.create_draft_pull_request(feature_branch, spec.base_branch, title).url


@dataclass(frozen=True)
class RepositoryConfig:
    """A user-declared repository: its checkout path and `owner/name`; one must carry `is_harness`.

    `worktree_root` hardcodes the harness's default worktrees root; only the `is_harness` entry sets it.
    """

    path: Path
    owner_repo: RepoTarget
    is_harness: bool = False
    worktree_root: Path | None = None


@dataclass(frozen=True)
class Repository(RepositoryData):
    """A `RepositoryConfig` resolved to its ready-to-use `PullRequests` and worktree root."""

    pull_requests: PullRequests


class RepositoryPoolError(Exception):
    """Raised when `RepositoryPool` is configured with zero or more than one harness repository."""


class RepositoryPool:
    """Every manually checked-out Repository, keyed by `owner/name`; built once from `RepositoryConfig`."""

    def __init__(self, configs: Sequence[RepositoryConfig], github_factory: GithubFactory) -> None:
        harness_configs = [config for config in configs if config.is_harness]
        if len(harness_configs) != 1:
            raise RepositoryPoolError(f"expected exactly one is_harness repository, found {len(harness_configs)}")
        harness_path = harness_configs[0].path
        repositories = [
            Repository(
                path=config.path,
                owner_repo=config.owner_repo,
                is_harness=config.is_harness,
                pull_requests=PullRequests(github_factory(config.path)),
                worktree_root=config.worktree_root
                or harness_path / "workspace" / f"{config.path.name}.worktrees",
            )
            for config in configs
        ]
        self._by_target = {repository.owner_repo.casefold(): repository for repository in repositories}
        self._harness = next(repository for repository in repositories if repository.is_harness)

    @property
    def harness(self) -> Repository:
        return self._harness

    def get(self, target: RepoTarget) -> Repository | None:
        """The Repository configured for `target`, or None when it has no entry in the pool."""
        return self._by_target.get(target.casefold())
