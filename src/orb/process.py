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

_CANCEL_POLL_S = 0.05

# Transient exit codes, retry count, and delay are copied from Sandcastle's sandbox lifecycle.
TRANSIENT_EXIT_CODES = frozenset({126, 137})
TRANSIENT_RETRIES = 2
TRANSIENT_RETRY_DELAY_S = 0.25


class CommandExecutor(Protocol):
    """Runs a command in a capsule's environment; a non-zero exit is returned, not raised."""

    def __call__(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: OnLine | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult: ...


def _label(args: Sequence[str] | str) -> str:
    return args if isinstance(args, str) else " ".join(args)


def _watch_cancel(cancel: threading.Event, done: threading.Event, process: subprocess.Popen) -> None:
    while not done.is_set():
        if cancel.wait(_CANCEL_POLL_S):
            process.terminate()
            return


def execute(
    args: Sequence[str] | str,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    on_line: OnLine | None = None,
    cancel: threading.Event | None = None,
) -> CommandResult:
    """Run a process without raising on a non-zero exit; a string runs through the shell.

    When on_line is given, each stdout line is delivered to it while the process is still
    running; returning a truthy value from on_line terminates the process early. When cancel
    is given, the process is terminated as soon as the event is set, mirroring the timeout.
    """
    if on_line is not None:
        return _execute_streaming(args, on_line, cwd=cwd, env=env, timeout_s=timeout_s, cancel=cancel)
    if cancel is None:
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
    return _execute_cancelable(args, cwd=cwd, env=env, timeout_s=timeout_s, cancel=cancel)


def _execute_cancelable(
    args: Sequence[str] | str,
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout_s: float | None,
    cancel: threading.Event,
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
        )
    except FileNotFoundError as exception:
        raise CommandError(_label(args), None, str(exception)) from exception

    done = threading.Event()
    watcher = threading.Thread(target=_watch_cancel, args=(cancel, done, process), daemon=True)
    watcher.start()

    timed_out = threading.Event()
    timer = None
    if timeout_s is not None:
        timer = threading.Timer(timeout_s, lambda: (timed_out.set(), process.terminate()))
        timer.start()

    stdout, stderr = process.communicate()
    done.set()
    watcher.join()
    if timer is not None:
        timer.cancel()

    if timed_out.is_set():
        raise CommandError(_label(args), None, f"timed out after {timeout_s}s")
    return CommandResult(process.returncode, stdout, stderr)


def _execute_streaming(
    args: Sequence[str] | str,
    on_line: OnLine,
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout_s: float | None,
    cancel: threading.Event | None = None,
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

    done = threading.Event()
    watcher = None
    if cancel is not None:
        watcher = threading.Thread(target=_watch_cancel, args=(cancel, done, process), daemon=True)
        watcher.start()

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
    done.set()
    if watcher is not None:
        watcher.join()
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
