from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from orb.contracts.capsule import CapsuleInstance, CapsuleProvider
from orb.errors import CommandError
from orb.process import CommandResult, execute
from orb.worktree import Worktree, create_worktree


class WorktreeCapsule(CapsuleInstance):
    """A git worktree whose commands run directly on the host with no isolation; permissions are the user's own."""

    def __init__(self, worktree: Worktree, env: Mapping[str, str] | None = None) -> None:
        self._worktree = worktree
        self._env = dict(env or {})

    @property
    def path(self) -> Path:
        return self._worktree.path

    @property
    def branch(self) -> str:
        return self._worktree.branch

    @property
    def host_branch(self) -> str:
        return self._worktree.host_branch

    def execute(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        return execute(command, cwd=cwd or self.path, env=self._env, timeout_s=timeout_s)

    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        result = self.execute(command, timeout_s=timeout_s)
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
        self._worktree.close()


class WorktreeCapsuleProvider(CapsuleProvider):
    """One git worktree per capsule; subclasses override `_attach` to isolate command execution."""

    def __init__(
        self, *, env: Mapping[str, str] | None = None, worktrees_dir: Path | None = None
    ) -> None:
        self._env = dict(env or {})
        self._worktrees_dir = worktrees_dir

    def open(self, repo: Path, branch: str | None) -> CapsuleInstance:
        worktree = create_worktree(repo=repo, branch=branch, worktrees_dir=self._worktrees_dir)
        try:
            return self._attach(worktree)
        except Exception:
            worktree.close()
            raise

    def _attach(self, worktree: Worktree) -> WorktreeCapsule:
        return WorktreeCapsule(worktree, self._env)


def worktree(
    *, env: Mapping[str, str] | None = None, worktrees_dir: Path | None = None
) -> WorktreeCapsuleProvider:
    return WorktreeCapsuleProvider(env=env, worktrees_dir=worktrees_dir)
