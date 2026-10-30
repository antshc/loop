"""The repository's configured check command, run in a worktree without a shell."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .process import CommandError, CommandRunner
from .process import Runner as CheckRunner

OUTPUT_LIMIT = 2000


class CheckCommand:
    """One check command run in a worktree through the process runner; a failure is its description and the tail of its output."""

    def __init__(self, command: Sequence[str], *, run: CheckRunner | None = None, timeout_s: float | None = None) -> None:
        if not command or any(not part for part in command):
            raise ValueError("a check command needs a non-empty argument array")
        self._command = tuple(command)
        self._run = run or CommandRunner()
        self._timeout_s = timeout_s

    def __call__(self, path: Path) -> str | None:
        """None when the command succeeds in `path`, else why it failed."""
        try:
            self._run(self._command, cwd=path, timeout_s=self._timeout_s)
        except CommandError as exception:
            return f"check `{' '.join(self._command)}` failed: {str(exception)[-OUTPUT_LIMIT:]}"
        return None
