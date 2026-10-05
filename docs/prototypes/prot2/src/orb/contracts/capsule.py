from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orb.process import CommandResult


@dataclass(frozen=True)
class Hook:
    command: str
    timeout_s: float | None = None


@dataclass(frozen=True)
class Hooks:
    on_capsule_ready: tuple[Hook, ...] = ()


class CapsuleInstance(ABC):
    """One isolated working environment bound to a branch."""

    @property
    @abstractmethod
    def path(self) -> Path: ...

    @property
    @abstractmethod
    def branch(self) -> str: ...

    @property
    @abstractmethod
    def host_branch(self) -> str: ...

    @abstractmethod
    def execute(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        """Run a command, defaulting to the capsule workspace; a non-zero exit is returned, not raised."""

    @abstractmethod
    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        """Run a shell command inside the capsule; raise CommandError on failure."""

    @abstractmethod
    def head(self) -> str: ...

    @abstractmethod
    def commits_since(self, revision: str) -> tuple[str, ...]: ...

    @abstractmethod
    def merge_into_host(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class CapsuleProvider(ABC):
    @abstractmethod
    def open(self, repo: Path, branch: str | None) -> CapsuleInstance:
        """Create the capsule on `branch`, or on a fresh branch when `branch` is None."""
