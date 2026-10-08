from __future__ import annotations

import threading
from pathlib import Path

from loop.errors import Cancelled, CommandError, HookError
from loop.platforms.git.branch_service import BranchService
from loop.platforms.git.client import Hook
from loop.platforms.git.commit_service import CommitService
from loop.platforms.git.facade import Git
from loop.platforms.git.objects import Branch, Commit, Worktree
from loop.platforms.git.worktree_service import WorktreeService


_BASE_COMMIT = "0" * 40


class FakeGit(Git):
    """Shared in-memory git state: fetches, remote branches, worktrees, leftovers, and Hook outcomes.

    `branches`, `worktrees`, and `commits` are fake views over this one state, so a commit
    made through one is visible to the others.
    """

    def __init__(self) -> None:
        self.fetched: list[Path] = []
        self.remote_branches: set[str] = set()
        self.hook_calls: list[str] = []
        self.failing_hooks: set[str] = set()
        self.cancelled_hooks: set[str] = set()
        self.failing_fetch: set[Path] = set()
        self.worktree_branches: dict[Path, str] = {}
        self.removed: list[Path] = []
        self.dirty_leftovers: set[Path] = set()
        self.branch_commits: dict[str, list[str]] = {}
        self.pushed: list[tuple[Path, str]] = []
        self.host_branch: str | None = "main"
        self.config: dict[str, str] = {}
        self.detached: set[Path] = set()
        self.merged: list[tuple[Path, str]] = []
        self.deleted_branches: list[str] = []
        self.subjects: dict[str, str] = {}
        self.dirty_worktrees: set[Path] = set()
        self.remote_heads: dict[str, str] = {}
        self.branches = FakeBranchService(self)
        self.worktrees = FakeWorktreeService(self)
        self.commits = FakeCommitService(self)
        # Compatibility aliases for callers that still address the service classes directly.
        self.branch_service = self.branches
        self.worktree_service = self.worktrees

    def commit(self, worktree: Path, subject: str, body: str = "") -> str:
        commits = self.branch_commits[self.worktree_branches[worktree]]
        commit = f"{len(commits) + 1:040d}"
        commits.append(commit)
        self.subjects[commit] = subject
        return commit


class FakeWorktreeService(WorktreeService):
    """A worktree view over `FakeGit`'s shared state, so create/list/remove need no git."""

    def __init__(self, fake: FakeGit) -> None:
        self._fake = fake

    def run_hook(self, hook: Hook, worktree: Path, cancel: threading.Event | None = None) -> None:
        self._fake.hook_calls.append(hook.command)
        if hook.command in self._fake.failing_hooks:
            raise HookError(hook.command, "fake hook failure")
        if hook.command in self._fake.cancelled_hooks:
            raise Cancelled()

    def create(self, branch: Branch, target: Path) -> Worktree:
        existing_branch = self._fake.worktree_branches.get(target)
        if existing_branch == branch.name:
            return Worktree(target, branch)
        other = next(
            (path for path, name in self._fake.worktree_branches.items() if name == branch.name and path != target), None
        )
        if other is not None:
            raise CommandError("git worktree add", None, f"branch {branch.name!r} is checked out in {other}")
        if target in self._fake.worktree_branches or target in self._fake.dirty_leftovers:
            if self.has_changes(Worktree(target)):
                raise CommandError("git worktree add", None, f"leftover worktree has uncommitted changes: {target}")
            self._fake.worktree_branches.pop(target, None)
            self._fake.dirty_leftovers.discard(target)
            self._fake.removed.append(target)
        self._fake.worktree_branches[target] = branch.name
        self._fake.branch_commits.setdefault(branch.name, [])
        return Worktree(target, branch)

    def list(self, checkout: Path) -> list[Worktree]:
        return [
            Worktree(path, None if path in self._fake.detached else Branch(path, branch))
            for path, branch in self._fake.worktree_branches.items()
        ] + [Worktree(path) for path in self._fake.dirty_leftovers]

    def get(self, path: Path) -> Worktree:
        if path in self._fake.worktree_branches:
            branch = None if path in self._fake.detached else Branch(path, self._fake.worktree_branches[path])
            return Worktree(path, branch)
        if path in self._fake.dirty_leftovers:
            return Worktree(path)
        host_branch = self._fake.host_branch
        branch = None if path in self._fake.detached or host_branch is None else Branch(path, host_branch)
        return Worktree(path, branch)

    def remove(self, worktree: Worktree, *, force: bool = False) -> None:
        if not force and self.has_changes(worktree):
            raise CommandError("git worktree remove", None, f"worktree has uncommitted changes: {worktree.path}")
        self._fake.worktree_branches.pop(worktree.path, None)
        self._fake.dirty_leftovers.discard(worktree.path)
        self._fake.removed.append(worktree.path)

    def detach(self, worktree: Worktree) -> Worktree:
        self._fake.detached.add(worktree.path)
        return Worktree(worktree.path, None)

    def has_changes(self, worktree: Worktree) -> bool:
        # fake: "changed" means the branch picked up a commit, not real working-tree dirt.
        if worktree.path in self._fake.dirty_leftovers:
            return True
        branch = self._fake.worktree_branches.get(worktree.path)
        return bool(self._fake.branch_commits[branch]) if branch is not None else False

    def is_clean(self, worktree: Worktree) -> bool:
        return worktree.path not in self._fake.dirty_worktrees


class FakeBranchService(BranchService):
    """A branch view over `FakeGit`'s shared commit/branch state, so prepare/publish/merge/delete need no git."""

    def __init__(self, fake: FakeGit) -> None:
        self._fake = fake

    def fetch(self, checkout: Path) -> None:
        if checkout in self._fake.failing_fetch:
            raise CommandError("git fetch", None, f"fetch failed: {checkout}")
        self._fake.fetched.append(checkout)

    def can_prepare(self, base: Branch) -> bool:
        return base.name in self._fake.remote_branches

    def prepare(self, branch: Branch | None, base: Branch, target: Path | None = None) -> Branch:
        checkout = base.path
        name = branch.name if branch is not None else self._generate_name()
        self._fake.branch_commits.setdefault(name, [])
        return Branch(checkout, name)

    def ahead_of_remote(self, branch: Branch, base: Branch) -> bool:
        if branch.name not in self._fake.branch_commits:
            return False
        commits = self._fake.branch_commits[branch.name]
        if branch.name in self._fake.remote_branches:
            upstream = self._fake.remote_heads.get(branch.name, _BASE_COMMIT)
        else:
            upstream = self._fake.remote_heads.get(base.name, _BASE_COMMIT)
        start = commits.index(upstream) + 1 if upstream in commits else 0
        return len(commits) > start

    def push(self, branch: Branch) -> None:
        commits = self._fake.branch_commits.get(branch.name, [])
        self._fake.pushed.append((branch.path, branch.name))
        self._fake.remote_branches.add(branch.name)
        self._fake.remote_heads[branch.name] = commits[-1] if commits else _BASE_COMMIT

    def merge(self, target: Path, branch: Branch) -> None:
        self._fake.merged.append((target, branch.name))

    def delete(self, branch: Branch) -> None:
        self._fake.deleted_branches.append(branch.name)
        self._fake.branch_commits.pop(branch.name, None)


class FakeCommitService(CommitService):
    """A commit view over `FakeGit`'s shared in-memory state, so an agent stand-in's commit is visible here."""

    def __init__(self, fake: FakeGit) -> None:
        self._fake = fake

    def head(self, path: Path) -> Commit:
        commits = self._fake.branch_commits[self._fake.worktree_branches[path]]
        sha = commits[-1] if commits else _BASE_COMMIT
        return Commit(path, sha, self._fake.subjects.get(sha, ""))

    def since(self, commit: Commit) -> list[Commit]:
        commits = self._fake.branch_commits[self._fake.worktree_branches[commit.path]]
        start = commits.index(commit.sha) + 1 if commit.sha in commits else 0
        return [Commit(commit.path, sha, self._fake.subjects.get(sha, "")) for sha in commits[start:]]

    def find_since(self, base: Branch, *, subject_prefix: str) -> list[Commit]:
        commits = self._fake.branch_commits[self._fake.worktree_branches[base.path]]
        upstream = self._fake.remote_heads.get(base.name) if base.name in self._fake.remote_branches else None
        start = commits.index(upstream) + 1 if upstream in commits else 0
        return [
            Commit(base.path, sha, self._fake.subjects[sha])
            for sha in commits[start:]
            if self._fake.subjects.get(sha, "").startswith(subject_prefix)
        ]

    def restore(self, commit: Commit) -> None:
        commits = self._fake.branch_commits[self._fake.worktree_branches[commit.path]]
        if commit.sha in commits:
            del commits[commits.index(commit.sha) + 1 :]
        else:
            commits.clear()
        self._fake.dirty_worktrees.discard(commit.path)

    def identity(self, path: Path) -> tuple[str, str]:
        return self._fake.config.get("user.name", ""), self._fake.config.get("user.email", "")
