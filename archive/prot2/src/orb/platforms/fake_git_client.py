from __future__ import annotations

from pathlib import Path

from orb.errors import CommandError
from orb.platforms.git_client import GitClient


class FakeGitClient(GitClient):
    """In-memory git: branches hold commit ids, and `commit()` lets a fake agent add work to a worktree."""

    def __init__(self, root: Path = Path("/fake/worktrees")) -> None:
        self._root = root
        self.branches: dict[str, list[str]] = {}
        self.worktrees: dict[Path, str] = {}
        self.merged: list[str] = []
        self.removed: list[Path] = []

    def create_branch(self, name: str, base: str = "HEAD") -> None:
        self.branches.setdefault(name, [])

    def create_worktree(self, branch: str) -> Path:
        if branch not in self.branches:
            raise CommandError("git worktree add", 128, f"unknown branch: {branch}")
        path = self._root / branch.replace("/", "__")
        self.worktrees[path] = branch
        return path

    def commit(self, worktree: Path, message: str) -> str:
        commits = self.branches[self.worktrees[worktree]]
        commit = f"{len(commits) + 1:040d}"
        commits.append(commit)
        return commit

    def head(self, worktree: Path) -> str:
        commits = self.branches[self.worktrees[worktree]]
        return commits[-1] if commits else "0" * 40

    def commits_since(self, worktree: Path, revision: str) -> tuple[str, ...]:
        commits = self.branches[self.worktrees[worktree]]
        return tuple(commits[commits.index(revision) + 1 :]) if revision in commits else tuple(commits)

    def merge_into_host(self, worktree: Path) -> None:
        self.merged.append(self.worktrees[worktree])

    def remove_worktree(self, worktree: Path) -> None:
        self.worktrees.pop(worktree)
        self.removed.append(worktree)
