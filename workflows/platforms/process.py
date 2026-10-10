"""Blocking subprocess helpers shared by workflow platform code."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path


class CommandError(Exception):
    def __init__(self, command: str, returncode: int | None, output: str) -> None:
        super().__init__(f"command failed ({returncode}): {command}\n{output}".rstrip())
        self.command = command
        self.returncode = returncode
        self.output = output


def run_command(args: Sequence[str], *, cwd: Path | None = None, timeout_s: float | None = None) -> str:
    """Runs a process and returns stdout; raises CommandError on a non-zero exit, timeout, or missing executable."""
    label = " ".join(args)
    try:
        completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exception:
        raise CommandError(label, None, f"timed out after {timeout_s}s") from exception
    except FileNotFoundError as exception:
        raise CommandError(label, None, str(exception)) from exception
    if completed.returncode != 0:
        raise CommandError(label, completed.returncode, completed.stdout + completed.stderr)
    return completed.stdout


def cli_runner(executable: str, *, cwd: Path | None = None) -> Callable[[tuple[str, ...]], str]:
    def call(args: tuple[str, ...]) -> str:
        return run_command((executable, *args), cwd=cwd)

    return call
