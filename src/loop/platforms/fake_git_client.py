from __future__ import annotations

import threading
from pathlib import Path

from loop.errors import Cancelled, CommandError, HookError
from loop.platforms.git_client import Branch, GitClient, Hook, Worktree


_BASE_COMMIT = "0" * 40


class FakeGitClient(GitClient):
    """In-memory git: simulates fetches, remote branches, worktrees, leftovers, and Hook outcomes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fetched: list[Path] = []
        self.remote_branches: set[str] = set()
        self.hook_calls: list[str] = []
        self.failing_hooks: set[str] = set()
        self.cancelled_hooks: set[str] = set()
        self.failing_fetch: set[Path] = set()
        self.worktrees: dict[Path, str] = {}
        self.removed: list[Path] = []
        self.dirty_leftovers: set[Path] = set()
        self.branches: dict[str, list[str]] = {}
        self.pushed: list[tuple[Path, str]] = []
        self.host_branch: str | None = "main"
        self.config: dict[str, str] = {}
        self.detached: set[Path] = set()
        self.merged: list[tuple[Path, str]] = []
        self.deleted_branches: list[str] = []
        self.subjects: dict[str, str] = {}
        self.dirty_worktrees: set[Path] = set()
        self.remote_heads: dict[str, str] = {}

    def fetch(self, checkout: Path) -> None:
        if checkout in self.failing_fetch:
            raise CommandError("git fetch", None, f"fetch failed: {checkout}")
        self.fetched.append(checkout)

    def remote_branch_exists(self, checkout: Path, branch: str) -> bool:
        return branch in self.remote_branches

    def check_branch_name(self, checkout: Path, branch: str) -> None:
        pass

    def exclude_workspace(self, harness_root: Path) -> None:
        pass

    def list_worktrees(self, checkout: Path) -> list[Worktree]:
        return [Worktree(path, Branch(branch)) for path, branch in self.worktrees.items()] + [
            Worktree(path) for path in self.dirty_leftovers
        ]

    def add_worktree(self, checkout: Path, target: Path, branch: str, start_ref: str) -> None:
        self.worktrees[target] = branch
        self.branches.setdefault(branch, [])

    def remove_worktree(self, checkout: Path, worktree: Path) -> None:
        self.worktrees.pop(worktree)
        self.removed.append(worktree)

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
        if worktree in self.dirty_leftovers:
            return True
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

    def head_subject(self, worktree: Path) -> str:
        return self.subjects.get(self.head(worktree), "")

    def is_clean(self, worktree: Path) -> bool:
        return worktree not in self.dirty_worktrees

    def reset_to(self, worktree: Path, commit: str) -> None:
        branch = self.worktrees[worktree]
        commits = self.branches[branch]
        if commit in commits:
            del commits[commits.index(commit) + 1 :]
        else:
            commits.clear()
        self.dirty_worktrees.discard(worktree)

    def commits_with_prefix(self, worktree: Path, range_spec: str, prefix: str) -> list[str]:
        commits = self.branches[self.worktrees[worktree]]
        base, _, tip = range_spec.partition("..")
        start = commits.index(base) + 1 if base in commits else 0
        end = commits.index(tip) + 1 if tip in commits else len(commits)
        return [
            f"{commit[-7:]} {self.subjects[commit]}"
            for commit in commits[start:end]
            if self.subjects.get(commit, "").startswith(prefix)
        ]

    def branch_ahead_of_remote(self, checkout: Path, branch: str, base: str) -> bool:
        if branch not in self.branches:
            return False
        commits = self.branches[branch]
        upstream = self.remote_heads.get(branch) if branch in self.remote_branches else base
        start = commits.index(upstream) + 1 if upstream in commits else 0
        return len(commits) > start

    def push(self, worktree: Path, branch: str) -> None:
        self.pushed.append((worktree, branch))
        commits = self.branches.get(branch, [])
        self.remote_branches.add(branch)
        self.remote_heads[branch] = commits[-1] if commits else _BASE_COMMIT
