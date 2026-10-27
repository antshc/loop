from __future__ import annotations

import logging
import secrets
import subprocess
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Protocol

from ..hooks import LoopHookPoint
from .errors import WorktreeError
from .git_cli import GitCli

logger = logging.getLogger(__name__)

# GitOptions holds a strategy and each strategy takes the options, so it is imported for annotations only.
if TYPE_CHECKING:
    from .options import GitOptions


class GitStrategy(Protocol):
    """Owns one git lifecycle around a run and yields the directory the agent works in."""

    def open(
        self, git: GitCli, cwd: Path, repository: Path, options: GitOptions
    ) -> AbstractContextManager[Path]: ...


@dataclass(frozen=True)
class HeadStrategy:
    """No worktree or branch: the agent works directly in the repository directory."""

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        yield repository


@dataclass(frozen=True)
class MergeToHeadStrategy:
    """Temp-branch worktree, fast-forward merged into the current HEAD on success, then the temp branch is deleted."""

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        branch = f"tmp_{secrets.token_hex(4)}"
        with _worktree(git, cwd, repository, options, branch, "HEAD") as target:
            yield target
        # Reached only when the run succeeded; on failure the temp branch is kept unmerged.
        git.merge_ff_only(repository, branch)
        git.delete_branch(repository, branch)


@dataclass(frozen=True)
class BranchStrategy:
    """Reuse a local branch; a new branch starts at explicit base, else origin branch or HEAD."""

    branch: str
    base_branch: str | None = None

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        git.fetch(repository)
        with _worktree(git, cwd, repository, options, self.branch, self.base_branch) as target:
            yield target


@contextmanager
def _worktree(
    git: GitCli, cwd: Path, repository: Path, options: GitOptions, branch: str, base: str | None
) -> Iterator[Path]:
    root = (cwd / options.root_path).resolve()
    if not options.root_path.is_absolute() and not root.is_relative_to(cwd.resolve()):
        raise ValueError(f"worktrees root {root} must be inside {cwd}")
    target = root / branch
    if not target.resolve().is_relative_to(root):
        raise WorktreeError(f"Worktree path escapes its root: {target}")
    # Validate before filesystem mutation; Git also rejects unsafe branch names.
    git.add_worktree(repository, target, branch, base)
    try:
        for hook in options.hooks_at(LoopHookPoint.WORKTREE_READY):
            git.run_hook(hook, target, repository, target)
        yield target
        for hook in options.hooks_at(LoopHookPoint.WORKTREE_REMOVING):
            git.run_hook(hook, target, repository, target)
    except BaseException as exception:
        try:
            _cleanup(git, repository, target)
        except (OSError, subprocess.CalledProcessError) as cleanup:
            note = f"worktree cleanup also failed; retained {target}: {cleanup}"
            if hasattr(exception, "add_note"):
                exception.add_note(note)
            else:
                logger.error(note, exc_info=True)
        raise
    else:
        if not _cleanup(git, repository, target):
            raise WorktreeError(f"Worktree has uncommitted changes; preserved at {target}")


def _cleanup(git: GitCli, repository: Path, target: Path) -> bool:
    if not git.is_clean(target):
        logger.warning("dirty worktree retained: %s", target)
        return False
    git.remove_worktree(repository, target)
    return True
