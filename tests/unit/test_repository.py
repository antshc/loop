from __future__ import annotations

from pathlib import Path

from workflows.platforms.work_tracking import GitHubRepo, RepositoryConfig, RepositoryPool
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def _github_factory(checkout: Path) -> GitHubRepo:
    return GitHubRepo("owner", "repo", gh=FakeGhCli())


def test_repository_pool_defaults_each_entrys_worktree_root_under_the_harness_workspace_folder(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "workspace" / "widgets"
    pool = RepositoryPool(
        [
            RepositoryConfig(path=tmp_path, owner_repo="owner/repo", is_harness=True),
            RepositoryConfig(path=clone, owner_repo="acme/widgets"),
        ],
        _github_factory,
    )

    assert pool.harness.worktree_root == tmp_path / "workspace" / f"{tmp_path.name}.worktrees"
    target = pool.get("acme/widgets")
    assert target is not None and target.worktree_root == tmp_path / "workspace" / "widgets.worktrees"


def test_repository_pool_keeps_an_explicit_worktree_root_from_its_config(tmp_path: Path) -> None:
    custom_root = tmp_path / "elsewhere" / "custom.worktrees"

    pool = RepositoryPool(
        [RepositoryConfig(path=tmp_path, owner_repo="owner/repo", is_harness=True, worktree_root=custom_root)],
        _github_factory,
    )

    assert pool.harness.worktree_root == custom_root
