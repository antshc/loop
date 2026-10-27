from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .state import Blocked


class Git:
    """Own workflow Git inspection, explicit commits, and non-forced publication."""

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

    def commit(self, path: Path, message: str) -> str:
        """Commit verified changes, returning the actual SHA."""
        self.text(path, "add", "-A")
        self.text(path, "commit", "-m", message)
        return self.head(path)

    def publish(self, path: Path, branch: str) -> None:
        """Publish recovery-branch commits to the PR branch without force."""
        self.text(path, "check-ref-format", f"refs/heads/{branch}")
        self.text(path, "push", "origin", f"HEAD:refs/heads/{branch}")

    def fetch_revision(self, path: Path, sha: str) -> None:
        """Fetch an expected PR revision without changing the host checkout."""
        self.text(path, "fetch", "origin", sha)

    def advance(self, path: Path, sha: str) -> None:
        """Fast-forward a known published recovery branch to the current PR head."""
        self.text(path, "merge", "--ff-only", sha)
