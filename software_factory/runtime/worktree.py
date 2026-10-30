"""The Git service the runtime collects evidence, commits and restores with; processes run behind an injected command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class GitCommand(Protocol):
    """Runs `git <args>` in a path, with `env` added to the environment, and returns stdout."""

    def __call__(self, args: Sequence[str], path: Path, *, env: Mapping[str, str] | None = None) -> str: ...


@dataclass(frozen=True)
class WorktreeCommit:
    """One commit on a worktree's branch."""

    sha: str
    subject: str


class WorktreeGit:
    """Reads and moves the branch of one worktree through `command`, which runs `git <args>` in a path and returns stdout."""

    def __init__(self, command: GitCommand) -> None:
        self._command = command

    def head(self, path: Path) -> str:
        """The commit the branch points at."""
        return self._command(("rev-parse", "HEAD"), path).strip()

    def status(self, path: Path) -> str:
        """The raw porcelain status, untracked files included."""
        return self._command(("status", "--porcelain", "--untracked-files=all"), path)

    def is_clean(self, path: Path) -> bool:
        """Whether the worktree has no staged, unstaged or untracked change."""
        return not self.status(path).strip()

    def parents(self, path: Path) -> tuple[str, ...]:
        """The parent commits of HEAD."""
        return tuple(self._command(("rev-list", "--parents", "-n", "1", "HEAD"), path).split()[1:])

    def is_ancestor(self, path: Path, sha: str) -> bool:
        """Whether `sha` is reachable from HEAD."""
        return not self._command(("rev-list", "-n", "1", sha, "^HEAD"), path).strip()

    def tree_id(self, path: Path) -> str:
        """A content id of the worktree: the tree of HEAD plus every change and untracked file, written through a throwaway index so nothing is staged."""
        git_dir = Path(self._command(("rev-parse", "--absolute-git-dir"), path).strip())
        index = git_dir / "factory-tree.index"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            self._command(("read-tree", "HEAD"), path, env=env)
            self._command(("add", "-A"), path, env=env)
            return self._command(("write-tree",), path, env=env).strip()
        finally:
            index.unlink(missing_ok=True)

    def commits_since(self, path: Path, sha: str) -> tuple[WorktreeCommit, ...]:
        """Commits after `sha` up to HEAD, oldest first."""
        output = self._command(("log", "--reverse", "--format=%H%x1f%s", f"{sha}..HEAD"), path)
        pairs = (line.split("\x1f", 1) for line in output.splitlines() if line.strip())
        return tuple(WorktreeCommit(sha=pair[0], subject=pair[1] if len(pair) > 1 else "") for pair in pairs)

    def commit(self, path: Path, message: str) -> str:
        """Stages every change, commits it with `message` and returns the new HEAD."""
        self._command(("add", "-A"), path)
        self._command(("commit", "-m", message), path)
        return self.head(path)

    def restore(self, path: Path, sha: str) -> None:
        """Hard-resets the worktree to `sha` and removes untracked files and directories."""
        self._command(("reset", "--hard", sha), path)
        self._command(("clean", "-fd"), path)
