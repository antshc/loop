from __future__ import annotations

import threading
from pathlib import Path

from loop.errors import Cancelled, CommandError, HookError
from loop.platforms.git.client import GitClient, Hook
from loop.platforms.git.commit_service import CommitService
from loop.platforms.git.objects import Branch, Commit, Worktree


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
        self.commits = FakeCommitService(self)

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
        return [Worktree(path, Branch(path, branch)) for path, branch in self.worktrees.items()] + [
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

    def current_branch(self, path: Path) -> str | None:
        if path in self.worktrees:
            return None if path in self.detached else self.worktrees[path]
        return self.host_branch

    def config_get(self, path: Path, key: str) -> str | None:
        return self.config.get(key)

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

    def is_clean(self, worktree: Path) -> bool:
        return worktree not in self.dirty_worktrees

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


class FakeCommitService(CommitService):
    """A commit view over `FakeGitClient`'s shared in-memory state, so an agent stand-in's commit is visible here."""

    def __init__(self, fake: FakeGitClient) -> None:
        self._fake = fake

    def head(self, path: Path) -> Commit:
        commits = self._fake.branches[self._fake.worktrees[path]]
        sha = commits[-1] if commits else _BASE_COMMIT
        return Commit(path, sha, self._fake.subjects.get(sha, ""))

    def since(self, commit: Commit) -> list[Commit]:
        commits = self._fake.branches[self._fake.worktrees[commit.path]]
        start = commits.index(commit.sha) + 1 if commit.sha in commits else 0
        return [Commit(commit.path, sha, self._fake.subjects.get(sha, "")) for sha in commits[start:]]

    def find_since(self, base: Branch, *, subject_prefix: str) -> list[Commit]:
        commits = self._fake.branches[self._fake.worktrees[base.path]]
        upstream = self._fake.remote_heads.get(base.name) if base.name in self._fake.remote_branches else None
        start = commits.index(upstream) + 1 if upstream in commits else 0
        return [
            Commit(base.path, sha, self._fake.subjects[sha])
            for sha in commits[start:]
            if self._fake.subjects.get(sha, "").startswith(subject_prefix)
        ]

    def restore(self, commit: Commit) -> None:
        commits = self._fake.branches[self._fake.worktrees[commit.path]]
        if commit.sha in commits:
            del commits[commits.index(commit.sha) + 1 :]
        else:
            commits.clear()
        self._fake.dirty_worktrees.discard(commit.path)

    def identity(self, path: Path) -> tuple[str, str]:
        return self._fake.config.get("user.name", ""), self._fake.config.get("user.email", "")
