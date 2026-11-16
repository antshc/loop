from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from loop import BranchStrategy, GitOptions
from loop.git import GitCli


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name)
    _git(path, "add", name)
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """An origin with branch `feat`, a clone of it and a second clone that pushes to it."""
    origin, checkout, other = (
        tmp_path / "origin",
        tmp_path / "checkout",
        tmp_path / "other",
    )
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _commit(other, "base")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/feat")
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    return checkout, other, tmp_path / "worktrees"


def _open(checkout: Path, root: Path) -> Path:
    options = GitOptions(root, checkout, BranchStrategy("feat", keep=True))
    with options.strategy.open(GitCli(), checkout, checkout, options) as path:
        return path


def test_kept_worktree_outlives_the_run_and_is_fast_forwarded_to_origin(
    repos: tuple[Path, Path, Path],
) -> None:
    checkout, other, root = repos
    path = _open(checkout, root)
    assert path.is_dir()

    _git(other, "checkout", "-q", "-b", "feat", "origin/feat")
    pushed = _commit(other, "next")
    _git(other, "push", "-q", "origin", "feat")

    assert _open(checkout, root) == path
    assert _git(path, "rev-parse", "HEAD") == pushed


def test_kept_worktree_with_local_commits_is_not_moved(
    repos: tuple[Path, Path, Path],
) -> None:
    checkout, other, root = repos
    path = _open(checkout, root)
    local = _commit(path, "local")
    _git(other, "checkout", "-q", "-b", "feat", "origin/feat")
    _commit(other, "next")
    _git(other, "push", "-q", "origin", "feat")

    _open(checkout, root)

    assert _git(path, "rev-parse", "HEAD") == local
