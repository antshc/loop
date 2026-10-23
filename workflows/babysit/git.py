from __future__ import annotations

import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..pr_maintenance.state import Blocked, PrState


class Git:
    """Own deterministic verification, commits, and non-forced publication."""

    def __init__(self, *, run=subprocess.run) -> None:
        self._run = run

    def text(self, path: Path, *args: str) -> str:
        """Execute a bounded Git command without shell interpolation."""
        return self._run(["git", "-C", str(path), *args], check=True, capture_output=True,
                         text=True, timeout=120).stdout.strip()

    def validate(self, path: Path, repo: str) -> None:
        """Check checkout identity before changing refs or worktrees."""
        remote = self.text(path, "remote", "get-url", "origin")
        slug = re.sub(r"\.git$", "", re.sub(r"^(git@github\.com:|https://github\.com/)", "", remote))
        if slug.lower() != repo.lower():
            raise Blocked(f"Origin does not match {repo}: {path}")

    def clean(self, path: Path) -> bool:
        """Include untracked files in the publication gate."""
        return not self.text(path, "status", "--porcelain")

    def head(self, path: Path) -> str:
        """Read the actual commit rather than trusting an agent report."""
        return self.text(path, "rev-parse", "HEAD")

    def verify(self, path: Path, repository, timeout: float) -> None:
        """Run the configured build and test commands before committing."""
        for command in (repository.build, repository.test):
            self._run(list(command), cwd=path, check=True, capture_output=True, text=True, timeout=timeout)

    def commit(self, path: Path, message: str) -> str:
        """Commit verified changes, returning the actual SHA."""
        self.text(path, "add", "-A")
        self.text(path, "commit", "-m", message)
        return self.head(path)

    def publish(self, path: Path, branch: str) -> None:
        """Publish detached worktree commits to the PR branch without force."""
        self.text(path, "check-ref-format", f"refs/heads/{branch}")
        self.text(path, "push", "origin", f"HEAD:refs/heads/{branch}")


@dataclass(frozen=True)
class PrWorktree:
    """A Loop GitStrategy that never makes safety-net commits or discards edits."""

    target: Path
    sha: str
    state: PrState
    commands: Git

    @contextmanager
    def open(self, git, cwd: Path, repository: Path, options) -> Iterator[Path]:
        """Reuse recovery work or create a detached PR worktree and preserve failures."""
        if self.target.exists():
            actual = Path(self.commands.text(self.target, "rev-parse", "--show-toplevel")).resolve()
            common = self.commands.text(self.target, "rev-parse", "--path-format=absolute", "--git-common-dir")
            expected = self.commands.text(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if actual != self.target or common != expected:
                raise Blocked(f"Unexpected worktree: {self.target}")
            if not self.state.data.get("batch"):
                if not self.commands.clean(self.target):
                    raise Blocked(f"Unreconciled recovery worktree: {self.target}")
                if self.commands.head(self.target) != self.sha:
                    if self.commands.head(self.target) != self.state.data.get("worktree_base"):
                        raise Blocked(f"Unpublished recovery commits: {self.target}")
                    git.remove_worktree(repository, self.target)
        if not self.target.exists():
            if self.state.data.get("batch"):
                raise Blocked("Publication checkpoint exists but its worktree is missing")
            self.commands.text(repository, "fetch", "origin", self.sha)
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self.commands.text(repository, "worktree", "add", "--detach", str(self.target), self.sha)
            self.state.data["worktree_base"] = self.sha
            self.state.save()
        try:
            yield self.target
        except BaseException:
            # Keep uncommitted and unpublished work for explicit recovery.
            raise
        else:
            if self.commands.clean(self.target) and not self.state.data.get("batch"):
                git.remove_worktree(repository, self.target)
