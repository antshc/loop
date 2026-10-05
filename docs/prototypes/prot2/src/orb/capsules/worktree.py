from __future__ import annotations

from pathlib import Path

from orb.contracts.capsule import CapsuleInstance, CapsuleProvider
from orb.contracts.sandbox import SandboxHandle, SandboxProvider
from orb.errors import CommandError
from orb.sandboxes.no_sandbox import no_sandbox
from orb.worktree import Worktree, create_worktree


class WorktreeCapsule(CapsuleInstance):
    def __init__(self, worktree: Worktree, sandbox: SandboxHandle) -> None:
        self._worktree = worktree
        self._sandbox = sandbox

    @property
    def path(self) -> Path:
        return self._worktree.path

    @property
    def branch(self) -> str:
        return self._worktree.branch

    @property
    def host_branch(self) -> str:
        return self._worktree.host_branch

    @property
    def sandbox(self) -> SandboxHandle:
        return self._sandbox

    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        result = self._sandbox.exec(command, timeout_s=timeout_s)
        if result.returncode != 0:
            raise CommandError(command, result.returncode, result.stdout + result.stderr)
        return result.stdout

    def head(self) -> str:
        return self._worktree.head()

    def commits_since(self, revision: str) -> tuple[str, ...]:
        return self._worktree.commits_since(revision)

    def merge_into_host(self) -> None:
        self._worktree.merge_into_host()

    def close(self) -> None:
        self._sandbox.close()
        self._worktree.close()


class WorktreeCapsuleProvider(CapsuleProvider):
    """A git worktree per capsule, with commands run through the given sandbox provider."""

    def __init__(
        self, *, sandbox: SandboxProvider | None = None, worktrees_dir: Path | None = None
    ) -> None:
        self._sandbox = sandbox or no_sandbox()
        self._worktrees_dir = worktrees_dir

    def open(self, repo: Path, branch: str | None) -> CapsuleInstance:
        worktree = create_worktree(repo=repo, branch=branch, worktrees_dir=self._worktrees_dir)
        try:
            handle = self._sandbox.create(worktree.path)
        except Exception:
            worktree.close()
            raise
        return WorktreeCapsule(worktree, handle)


def worktree(
    *, sandbox: SandboxProvider | None = None, worktrees_dir: Path | None = None
) -> WorktreeCapsuleProvider:
    return WorktreeCapsuleProvider(sandbox=sandbox, worktrees_dir=worktrees_dir)
