from __future__ import annotations

from pathlib import Path


class OrbError(Exception):
    """Base class for every error the library raises on purpose."""


class CommandError(OrbError):
    def __init__(self, command: str, returncode: int | None, output: str) -> None:
        super().__init__(f"command failed ({returncode}): {command}\n{output}".rstrip())
        self.command = command
        self.returncode = returncode
        self.output = output


class PromptError(OrbError):
    pass


class AgentError(OrbError):
    def __init__(self, name: str, output: str) -> None:
        super().__init__(f"agent '{name}' failed:\n{output}".rstrip())
        self.name = name
        self.output = output


class ExtractionError(OrbError):
    pass


class ExecutionStoreError(OrbError):
    pass


class HookError(OrbError):
    def __init__(self, command: str, output: str) -> None:
        super().__init__(f"hook failed ({command}):\n{output}".rstrip())
        self.command = command
        self.output = output


class Cancelled(OrbError):
    """A run was cancelled; `worktree` carries its location when a dirty worktree was kept."""

    def __init__(self, worktree: Path | None = None) -> None:
        suffix = f" (worktree kept at {worktree})" if worktree else ""
        super().__init__(f"run cancelled{suffix}")
        self.worktree = worktree
