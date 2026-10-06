from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from orb.errors import CommandError, HookError
from orb.platforms.git_client import GitClient, Hook


class FakeGitClient(GitClient):
    """In-memory git: simulates fetches, remote branches, leftovers, branch-in-use, and Hook outcomes."""

    def __init__(self, root: Path = Path("/fake/worktrees")) -> None:
        self._root = root
        self.fetched: list[Path] = []
        self.remote_branches: set[str] = set()
        self.branch_in_use: dict[str, Path] = {}
        self.dirty_leftovers: set[Path] = set()
        self.hook_calls: list[str] = []
        self.failing_hooks: set[str] = set()
        self.failing_fetch: set[Path] = set()
        self.worktrees: dict[Path, str] = {}
        self.branches: dict[str, list[str]] = {}
        self.pushed: list[tuple[Path, str]] = []
        self.removed: list[Path] = []

    def fetch(self, checkout: Path) -> None:
        if checkout in self.failing_fetch:
            raise CommandError("git fetch", None, f"fetch failed: {checkout}")
        self.fetched.append(checkout)

    def remote_branch_exists(self, checkout: Path, branch: str) -> bool:
        return branch in self.remote_branches

    def create_worktree(
        self, checkout: Path, branch: str, base: str, harness_root: Path, *, on_ready: Sequence[Hook] = ()
    ) -> Path:
        if branch in self.branch_in_use:
            raise CommandError(
                "git worktree add", None, f"branch {branch!r} is checked out in {self.branch_in_use[branch]}"
            )
        path = self._root / branch.replace("/", "__")
        if path in self.dirty_leftovers:
            raise CommandError("git worktree add", None, f"leftover worktree has uncommitted changes: {path}")
        self.dirty_leftovers.discard(path)
        self.worktrees[path] = branch
        self.branches.setdefault(branch, [])
        try:
            for hook in on_ready:
                self.hook_calls.append(hook.command)
                if hook.command in self.failing_hooks:
                    raise HookError(hook.command, "fake hook failure")
        except HookError:
            self.remove_worktree(path)
            raise
        return path

    def has_changes(self, worktree: Path) -> bool:
        # fake: "changed" means the branch picked up a commit, not real working-tree dirt.
        return bool(self.branches[self.worktrees[worktree]])

    def commit(self, worktree: Path, subject: str, body: str = "") -> str:
        commits = self.branches[self.worktrees[worktree]]
        commit = f"{len(commits) + 1:040d}"
        commits.append(commit)
        return commit

    def push(self, worktree: Path, branch: str) -> None:
        self.pushed.append((worktree, branch))

    def remove_worktree(self, worktree: Path) -> None:
        self.worktrees.pop(worktree)
        self.removed.append(worktree)
