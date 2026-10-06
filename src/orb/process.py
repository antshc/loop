from __future__ import annotations

import os
import subprocess
import threading
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


# Returning True from an on_line callback requests early termination of the process.
OnLine = Callable[[str], "bool | None"]


class CommandExecutor(Protocol):
    """Runs a command in a capsule's environment; a non-zero exit is returned, not raised."""

    def __call__(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: OnLine | None = None,
    ) -> CommandResult: ...


def _label(args: Sequence[str] | str) -> str:
    return args if isinstance(args, str) else " ".join(args)


def execute(
    args: Sequence[str] | str,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    on_line: OnLine | None = None,
) -> CommandResult:
    """Run a process without raising on a non-zero exit; a string runs through the shell.

    When on_line is given, each stdout line is delivered to it while the process is still
    running; returning a truthy value from on_line terminates the process early.
    """
    if on_line is not None:
        return _execute_streaming(args, on_line, cwd=cwd, env=env, timeout_s=timeout_s)
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


def _execute_streaming(
    args: Sequence[str] | str,
    on_line: OnLine,
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout_s: float | None,
) -> CommandResult:
    try:
        process = subprocess.Popen(
            args,
            shell=isinstance(args, str),
            cwd=cwd,
            env=None if env is None else {**os.environ, **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exception:
        raise CommandError(_label(args), None, str(exception)) from exception

    stderr_chunks: list[str] = []
    stderr_thread = threading.Thread(target=lambda: stderr_chunks.append(process.stderr.read()))
    stderr_thread.start()

    timed_out = threading.Event()
    timer = None
    if timeout_s is not None:
        timer = threading.Timer(timeout_s, lambda: (timed_out.set(), process.terminate()))
        timer.start()

    stopped = False
    stdout_chunks: list[str] = []
    for line in process.stdout:
        stdout_chunks.append(line)
        if on_line(line.rstrip("\n")):
            stopped = True
            process.terminate()
            break
    process.stdout.close()
    process.wait()
    if timer is not None:
        timer.cancel()
    stderr_thread.join()

    if timed_out.is_set() and not stopped:
        raise CommandError(_label(args), None, f"timed out after {timeout_s}s")
    return CommandResult(process.returncode, "".join(stdout_chunks), stderr_chunks[0] if stderr_chunks else "")


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
