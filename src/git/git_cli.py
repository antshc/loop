from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..hooks import LoopHook, LoopHookError
from .errors import WorktreeError

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

    def add_worktree(self, repository: Path, target: Path, branch: str, base: str | None = None) -> None:
        """Create or validate a named worktree without resetting existing local commits."""
        self._git(repository, "check-ref-format", "--branch", branch)
        if target.exists():
            if self._reuse_worktree(repository, target, branch):
                return
        self._git(repository, "worktree", "prune", "--expire", "now")
        if not self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            remote = base is None and self._succeeds(
                repository, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
            self._git(repository, "branch", branch, f"origin/{branch}" if remote else (base or "HEAD"))
        logger.info("adding worktree %s on branch %s", target, branch)
        self._git(repository, "worktree", "add", str(target), branch)

    def _reuse_worktree(self, repository: Path, target: Path, branch: str) -> bool:
        if target.resolve() == repository.resolve():
            raise WorktreeError(f"Cannot manage the host checkout as a linked worktree: {target}")
        if not target.is_dir() or target.is_symlink():
            raise WorktreeError(f"Invalid worktree path: {target}")
        entries = list(target.iterdir())
        if not entries:
            target.rmdir()
            return False
        probe = self._invoke(target, ("rev-parse", "--show-toplevel"), check=False)
        if probe.returncode != 0:
            # Only an empty directory or a broken link without user files is disposable.
            if len(entries) == 1 and entries[0].name == ".git" and entries[0].is_file():
                text = entries[0].read_text().strip()
                common = Path(self._output(repository, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                if text.startswith("gitdir: "):
                    link = (target / text[8:]).resolve()
                    if link.parent == common.resolve() / "worktrees" and not link.exists():
                        entries[0].unlink()
                        target.rmdir()
                        return False
            raise WorktreeError(f"Nonempty orphan worktree preserved for recovery: {target}")
        if Path(probe.stdout.strip()).resolve() != target.resolve():
            raise WorktreeError(f"Unexpected worktree root: {target}")
        common_args = ("rev-parse", "--path-format=absolute", "--git-common-dir")
        if self._output(target, *common_args) != self._output(repository, *common_args):
            raise WorktreeError(f"Worktree belongs to another repository: {target}")
        records = self._output(repository, "worktree", "list", "--porcelain", "-z").split("\0\0")
        registered = next((record.split("\0") for record in records
                           if f"worktree {target.resolve()}" in record.split("\0")), None)
        if registered is None or any(field.startswith("locked") for field in registered):
            raise WorktreeError(f"Worktree is unregistered or administratively locked: {target}")
        actual = self._invoke(target, ("symbolic-ref", "--quiet", "--short", "HEAD"), check=False)
        if actual.returncode != 0 or actual.stdout.strip() != branch:
            raise WorktreeError(f"Worktree branch does not match {branch}: {target}")
        logger.info("reusing worktree %s on branch %s", target, branch)
        return True

    def is_clean(self, worktree: Path) -> bool:
        """Inspect tracked and untracked edits solely to decide lifecycle cleanup."""
        return not self._output(worktree, "status", "--porcelain", "--untracked-files=all")

    def _output(self, repository: Path, *args: str) -> str:
        return self._invoke(repository, args, check=True).stdout.strip()

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

