from __future__ import annotations

from pathlib import Path

from orb.errors import CommandError
from orb.process import execute, run_command
from orb.worktree import Worktree, create_worktree


class GitClient:
    """Branches, worktrees, and commit queries for one repository."""

    def __init__(self, repo: Path, *, worktrees_dir: Path | None = None) -> None:
        self._repo = repo
        self._worktrees_dir = worktrees_dir
        self._worktrees: dict[Path, Worktree] = {}

    def create_branch(self, name: str, base: str = "HEAD") -> None:
        """Create the branch from `base` unless it already exists locally."""
        # Rejects option-like and malformed names, so a branch from agent output is safe to pass to git.
        run_command(("git", "check-ref-format", "--branch", name), cwd=self._repo)
        exists = execute(("git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"), cwd=self._repo)
        if exists.returncode != 0:
            run_command(("git", "branch", name, base), cwd=self._repo)

    def create_worktree(self, branch: str) -> Path:
        worktree = create_worktree(repo=self._repo, branch=branch, worktrees_dir=self._worktrees_dir)
        self._worktrees[worktree.path] = worktree
        return worktree.path

    def head(self, worktree: Path) -> str:
        return self._tracked(worktree).head()

    def commits_since(self, worktree: Path, revision: str) -> tuple[str, ...]:
        return self._tracked(worktree).commits_since(revision)

    def merge_into_host(self, worktree: Path) -> None:
        self._tracked(worktree).merge_into_host()

    def remove_worktree(self, worktree: Path) -> None:
        self._tracked(worktree).close()
        self._worktrees.pop(worktree, None)

    def _tracked(self, worktree: Path) -> Worktree:
        try:
            return self._worktrees[worktree]
        except KeyError as exception:
            raise CommandError("git", None, f"unknown worktree: {worktree}") from exception
