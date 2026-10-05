from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path

from orb.process import CommandResult


class SandboxHandle(ABC):
    """Where commands for one worktree actually run."""

    @property
    @abstractmethod
    def worktree_path(self) -> Path: ...

    @abstractmethod
    def exec(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        """Run a command, defaulting to the worktree; a non-zero exit is returned, not raised."""

    @abstractmethod
    def close(self) -> None: ...


class SandboxProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def create(self, worktree_path: Path, env: Mapping[str, str] | None = None) -> SandboxHandle:
        """Open a sandbox over an existing worktree; `env` merges over the provider's own."""
