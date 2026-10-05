from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from orb.contracts.sandbox import SandboxHandle, SandboxProvider
from orb.process import CommandResult, execute


class NoSandboxHandle(SandboxHandle):
    def __init__(self, worktree_path: Path, env: Mapping[str, str]) -> None:
        self._worktree_path = worktree_path
        self._env = dict(env)

    @property
    def worktree_path(self) -> Path:
        return self._worktree_path

    def exec(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        return execute(command, cwd=cwd or self._worktree_path, env=self._env, timeout_s=timeout_s)

    def close(self) -> None:
        pass


class NoSandboxProvider(SandboxProvider):
    """Runs commands directly on the host with no isolation; permissions are the user's own."""

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(env or {})

    @property
    def name(self) -> str:
        return "no-sandbox"

    def create(self, worktree_path: Path, env: Mapping[str, str] | None = None) -> SandboxHandle:
        return NoSandboxHandle(worktree_path, {**self._env, **(env or {})})


def no_sandbox(*, env: Mapping[str, str] | None = None) -> NoSandboxProvider:
    return NoSandboxProvider(env=env)
