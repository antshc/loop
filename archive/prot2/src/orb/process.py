from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orb.errors import CommandError


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandExecutor(Protocol):
    """Runs a command in a capsule's environment; a non-zero exit is returned, not raised."""

    def __call__(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None
    ) -> CommandResult: ...


def _label(args: Sequence[str] | str) -> str:
    return args if isinstance(args, str) else " ".join(args)


def execute(
    args: Sequence[str] | str,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
) -> CommandResult:
    """Run a process without raising on a non-zero exit; a string runs through the shell."""
    try:
        completed = subprocess.run(
            args,
            shell=isinstance(args, str),
            cwd=cwd,
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exception:
        raise CommandError(_label(args), None, f"timed out after {timeout_s}s") from exception
    except FileNotFoundError as exception:
        raise CommandError(_label(args), None, str(exception)) from exception
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_command(
    args: Sequence[str] | str,
    *,
    cwd: Path | None = None,
    timeout_s: float | None = None,
) -> str:
    """Run a process and return stdout; raise CommandError on a non-zero exit."""
    result = execute(args, cwd=cwd, timeout_s=timeout_s)
    if result.returncode != 0:
        raise CommandError(_label(args), result.returncode, result.stdout + result.stderr)
    return result.stdout


def checked_output(command: str, result: CommandResult) -> str:
    """Return stdout of a finished command; raise CommandError on a non-zero exit."""
    if result.returncode != 0:
        raise CommandError(command, result.returncode, result.stdout + result.stderr)
    return result.stdout


def cli_runner(executable: str, *, cwd: Path | None = None) -> Callable[[tuple[str, ...]], str]:
    def call(args: tuple[str, ...]) -> str:
        return run_command((executable, *args), cwd=cwd)

    return call
