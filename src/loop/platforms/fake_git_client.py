from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from loop.errors import Cancelled, CommandError, HookError
from loop.platforms.git_client import GitClient, Hook


_BASE_COMMIT = "0" * 40


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
        self.cancelled_hooks: set[str] = set()
        self.failing_fetch: set[Path] = set()
        self.worktrees: dict[Path, str] = {}
        self.branches: dict[str, list[str]] = {}
        self.pushed: list[tuple[Path, str]] = []
        self.removed: list[Path] = []
        self.host_branch: str | None = "main"
        self.config: dict[str, str] = {}
        self.detached: set[Path] = set()
        self.merged: list[tuple[Path, str]] = []
        self.deleted_branches: list[str] = []
        self.subjects: dict[str, str] = {}

    def fetch(self, checkout: Path) -> None:
        if checkout in self.failing_fetch:
            raise CommandError("git fetch", None, f"fetch failed: {checkout}")
        self.fetched.append(checkout)

    def remote_branch_exists(self, checkout: Path, branch: str) -> bool:
        return branch in self.remote_branches

    def create_worktree(
        self,
        checkout: Path,
        branch: str,
        base: str,
        harness_root: Path,
        *,
        on_ready: Sequence[Hook] = (),
        cancel: threading.Event | None = None,
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
                self.run_hook(hook, path, cancel)
        except HookError:
            self.remove_worktree(path)
            raise
        except Cancelled:
            if self.has_changes(path):
                raise Cancelled(path)
            self.remove_worktree(path)
            raise
        return path

    def run_hook(self, hook: Hook, worktree: Path, cancel: threading.Event | None = None) -> None:
        self.hook_calls.append(hook.command)
        if hook.command in self.failing_hooks:
            raise HookError(hook.command, "fake hook failure")
        if hook.command in self.cancelled_hooks:
            raise Cancelled()

    def head(self, worktree: Path) -> str:
        commits = self.branches[self.worktrees[worktree]]
        return commits[-1] if commits else _BASE_COMMIT

    def current_branch(self, path: Path) -> str | None:
        if path in self.worktrees:
            return None if path in self.detached else self.worktrees[path]
        return self.host_branch

    def config_get(self, path: Path, key: str) -> str | None:
        return self.config.get(key)

    def commits_between(self, worktree: Path, base: str, tip: str = "HEAD") -> list[str]:
        commits = self.branches[self.worktrees[worktree]]
        start = commits.index(base) + 1 if base in commits else 0
        end = commits.index(tip) + 1 if tip in commits else len(commits)
        return commits[start:end]

    def merge(self, checkout: Path, branch: str) -> None:
        self.merged.append((checkout, branch))

    def detach(self, worktree: Path) -> None:
        self.detached.add(worktree)

    def delete_branch(self, checkout: Path, branch: str) -> None:
        self.deleted_branches.append(branch)
        self.branches.pop(branch, None)

    def has_changes(self, worktree: Path) -> bool:
        # fake: "changed" means the branch picked up a commit, not real working-tree dirt.
        return bool(self.branches[self.worktrees[worktree]])

    def commit(self, worktree: Path, subject: str, body: str = "") -> str:
        commits = self.branches[self.worktrees[worktree]]
        commit = f"{len(commits) + 1:040d}"
        commits.append(commit)
        self.subjects[commit] = subject
        return commit

    def recent_commits(self, worktree: Path, prefix: str, limit: int) -> list[str]:
        commits = self.branches[self.worktrees[worktree]]
        matching = [
            f"{commit[-7:]} {self.subjects[commit]}"
            for commit in reversed(commits)
            if self.subjects.get(commit, "").startswith(prefix)
        ]
        return matching[:limit]

    def push(self, worktree: Path, branch: str) -> None:
        self.pushed.append((worktree, branch))

    def remove_worktree(self, worktree: Path) -> None:
        self.worktrees.pop(worktree)
        self.removed.append(worktree)
