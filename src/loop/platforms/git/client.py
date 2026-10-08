from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loop.errors import Cancelled, CommandError, HookError
from loop.platforms.git.objects import Branch
from loop.process import CommandResult, checked_output, execute

DEFAULT_HOOK_TIMEOUT_S = 120.0


class GitRunner(Protocol):
    """Runs one git (or Hook) command in a given directory; a non-zero exit is returned, not raised."""

    def __call__(
        self,
        args: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class Hook:
    """A `worktree-ready` shell command; `timeout_s` overrides the default for this Hook."""

    command: str
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S


class GitClient:
    """Internal git command runner: fetch, branch, Hooks, commit, push; worktree policy lives in WorktreeService."""

    def __init__(self, *, run: GitRunner = execute) -> None:
        self._execute = run
        self._lock = threading.Lock()

    @property
    def lock(self) -> threading.Lock:
        """Serializes operations that mutate a checkout's refs and worktree list."""
        return self._lock

    def remote_branch_exists(self, checkout: Path, branch: str) -> bool:
        return self._show_ref(checkout, Branch(checkout, branch).remote_ref)

    def check_branch_name(self, checkout: Path, branch: str) -> None:
        self._run(("git", "check-ref-format", "--branch", branch), cwd=checkout)

    def config_get(self, path: Path, key: str) -> str | None:
        result = self._execute(("git", "config", "--get", key), cwd=path)
        return result.stdout.strip() or None if result.returncode == 0 else None

    def commit(self, worktree: Path, subject: str, body: str = "") -> None:
        self._run(("git", "add", "-A"), cwd=worktree)
        args = ("git", "commit", "-m", subject, "-m", body) if body else ("git", "commit", "-m", subject)
        self._run(args, cwd=worktree)

    def _show_ref(self, checkout: Path, ref: str) -> bool:
        return self._execute(("git", "show-ref", "--verify", "--quiet", ref), cwd=checkout).returncode == 0

    def run_hook(self, hook: Hook, worktree: Path, cancel: threading.Event | None = None) -> None:
        try:
            result = self._execute(hook.command, cwd=worktree, timeout_s=hook.timeout_s, cancel=cancel)
        except CommandError as exception:
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exception
            raise HookError(hook.command, str(exception)) from exception
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if result.returncode != 0:
            raise HookError(hook.command, result.stdout + result.stderr)

    def _run(self, args: Sequence[str] | str, *, cwd: Path) -> str:
        result = self._execute(args, cwd=cwd)
        return checked_output(args if isinstance(args, str) else " ".join(args), result)
