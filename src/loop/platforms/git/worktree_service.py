from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from loop.errors import CommandError
from loop.platforms.git.client import GitClient
from loop.platforms.git.objects import Branch

_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


class WorktreeService:
    """Creates, tracks, and removes the worktrees of Codebase Checkouts, and names their branches and folders."""

    def __init__(self, git: GitClient) -> None:
        self._git = git
        # worktree -> the checkout it was created from, so `remove` needs only the worktree.
        self._checkouts: dict[Path, Path] = {}

    @staticmethod
    def new_branch_name() -> str:
        return f"loop/sandbox-{uuid4().hex[:8]}"

    @staticmethod
    def feature_branch_name(base_branch: str, title: str) -> str:
        """`<version_with_underscores>_<title slug>` when base_branch carries a version, else `<title slug>`."""
        slug = slugify(title)
        match = _VERSION.search(base_branch)
        return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"

    @staticmethod
    def worktree_path(checkout: Path, harness_root: Path, branch: str) -> Path:
        return harness_root / "workspace" / f"{checkout.name}.worktrees" / branch

    def create(self, checkout: Path, branch: str, base: str, harness_root: Path) -> Path:
        """Checks out `branch` in a new worktree of `checkout`, replacing a clean leftover at the same path."""
        self._git.check_branch_name(checkout, branch)
        target = self.worktree_path(checkout, harness_root, branch)
        wanted = Branch(checkout, branch)
        with self._git.lock:
            worktrees = self._git.list_worktrees(checkout)
            other = next(
                (worktree.path for worktree in worktrees if worktree.branch == wanted and worktree.path != target),
                None,
            )
            if other is not None:
                raise CommandError("git worktree add", None, f"branch {branch!r} is checked out in {other}")
            if any(worktree.path == target for worktree in worktrees):
                if self._git.has_changes(target):
                    raise CommandError(
                        "git worktree add", None, f"leftover worktree has uncommitted changes: {target}"
                    )
                self._git.remove_worktree(checkout, target)

            start_ref = (
                wanted.upstream if self._git.remote_branch_exists(checkout, branch) else Branch(checkout, base).upstream
            )
            self._git.add_worktree(checkout, target, branch, start_ref)
            self._checkouts[target] = checkout
            self._git.exclude_workspace(harness_root)
        return target

    def remove(self, worktree: Path) -> None:
        try:
            checkout = self._checkouts.pop(worktree)
        except KeyError as exception:
            raise CommandError("git worktree remove", None, f"unknown worktree: {worktree}") from exception
        with self._git.lock:
            self._git.remove_worktree(checkout, worktree)
