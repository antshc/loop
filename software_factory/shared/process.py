"""Blocking subprocess execution behind one injectable runner."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol


class CommandError(subprocess.CalledProcessError):
    """A command exited non-zero, timed out, or could not start; `output` holds stdout and stderr."""

    def __init__(self, command: str, returncode: int | None, output: str) -> None:
        super().__init__(-1 if returncode is None else returncode, command, output)
        self.command = command
        self.exit_code = returncode

    def __str__(self) -> str:
        return f"command failed ({self.exit_code}): {self.command}\n{self.output}".rstrip()


class Runner(Protocol):
    """Runs one process and returns its stdout; raises `CommandError` on failure."""

    def __call__(self, args: Sequence[str], *, cwd: Path | None = None, timeout_s: float | None = None) -> str: ...


class CommandRunner:
    """Runs processes without a shell; `timeout_s` bounds every call that sets none of its own."""

    def __init__(self, *, timeout_s: float | None = None) -> None:
        self._timeout_s = timeout_s

    def __call__(self, args: Sequence[str], *, cwd: Path | None = None, timeout_s: float | None = None) -> str:
        label = " ".join(args)
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        try:
            completed = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exception:
            raise CommandError(label, None, f"timed out after {timeout}s") from exception
        except FileNotFoundError as exception:
            raise CommandError(label, None, str(exception)) from exception
        if completed.returncode != 0:
            raise CommandError(label, completed.returncode, completed.stdout + completed.stderr)
        return completed.stdout


def git_command(args: Sequence[str], path: Path, *, env: Mapping[str, str] | None = None) -> str:
    """Runs `git <args>` in `path` with `env` added to the environment and returns stdout; the runtime's Git command."""
    label = f"git {' '.join(args)}"
    environment = {**os.environ, **env} if env else None
    try:
        completed = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False, env=environment)
    except FileNotFoundError as exception:
        raise CommandError(label, None, str(exception)) from exception
    if completed.returncode != 0:
        raise CommandError(label, completed.returncode, completed.stdout + completed.stderr)
    return completed.stdout
