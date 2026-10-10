from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..hooks import LoopHook, LoopHookError

logger = logging.getLogger(__name__)


class GitCli:
    """Runs git against a given repository; `run` is injectable for testing."""

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._run = run

    def fetch(self, repository: Path) -> None:
        logger.info("fetching %s", repository)
        self._git(repository, "fetch", "--all", "--prune")

    def add_worktree(self, repository: Path, target: Path, branch: str, base: str = "HEAD") -> None:
        """Validate `branch`, reuse it if it exists locally, else create it, then attach a worktree at `target`."""
        self._git(repository, "check-ref-format", "--branch", branch)
        if not self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            remote = self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
            self._git(repository, "branch", branch, f"origin/{branch}" if remote else base)
        logger.info("adding worktree %s on branch %s", target, branch)
        self._git(repository, "worktree", "add", str(target), branch)

    def remove_worktree(self, repository: Path, target: Path, *, force: bool = False) -> None:
        """Detach the worktree at `target`; git refuses when it has uncommitted changes unless `force`. The branch is kept."""
        flags = ["--force"] if force else []
        logger.info("removing worktree %s", target)
        self._git(repository, "worktree", "remove", *flags, str(target))

    def run_hook(self, hook: LoopHook, cwd: Path, repository: Path, worktree: Path) -> None:
        logger.debug("loop hook %s: %s", hook.point.value, hook.command)
        try:
            result = self._run(
                hook.command,
                shell=True,
                cwd=cwd,
                timeout=hook.timeout_sec,
                env={
                    **os.environ,
                    "LOOP_REPOSITORY": str(repository),
                    "LOOP_WORKTREE": str(worktree),
                    "LOOP_HOOK_POINT": hook.point.value,
                },
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise LoopHookError(hook.point, hook.command, f"timed out after {hook.timeout_sec}s") from None
        if result.returncode != 0:
            raise LoopHookError(hook.point, hook.command, (result.stdout or "") + (result.stderr or ""))

    def commit_all(self, worktree: Path, message: str) -> None:
        """Commit every change in the worktree; no-op when there is nothing to commit."""
        self._git(worktree, "add", "-A")
        if not self._succeeds(worktree, "diff", "--cached", "--quiet"):
            logger.info("committing changes in %s", worktree)
            self._git(worktree, "commit", "-m", message)

    def merge_ff_only(self, repository: Path, branch: str) -> None:
        logger.info("merging %s fast-forward only", branch)
        self._git(repository, "merge", "--ff-only", branch)

    def delete_branch(self, repository: Path, branch: str) -> None:
        logger.info("deleting branch %s", branch)
        self._git(repository, "branch", "-d", branch)

    def _succeeds(self, repository: Path, *args: str) -> bool:
        return self._invoke(repository, args, check=False).returncode == 0

    def _git(self, repository: Path, *args: str) -> None:
        self._invoke(repository, args, check=True)

    def _invoke(
        self, repository: Path, args: tuple[str, ...], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repository), *args]
        logger.debug("git: %s", command)
        result = self._run(command, check=check, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug("git exited %s: %s", result.returncode, (result.stderr or "").strip())
        return result
