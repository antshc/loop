from __future__ import annotations

import secrets
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Protocol

from ..hooks import LoopHookPoint
from .git_cli import GitCli

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
            # Safety net: the prompt normally commits; this catches edits it left behind before the worktree is removed.
            git.commit_all(target, options.commit_message)
        # Reached only when the run succeeded; on failure the temp branch is kept unmerged.
        git.merge_ff_only(repository, branch)
        git.delete_branch(repository, branch)


@dataclass(frozen=True)
class BranchStrategy:
    """Worktree on the named branch; `base_branch` is the start ref for a new branch (default HEAD)."""

    branch: str
    base_branch: str | None = None

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        git.fetch(repository)
        with _worktree(git, cwd, repository, options, self.branch, self.base_branch or "HEAD") as target:
            yield target
            # Safety net: git refuses to remove a dirty worktree.
            git.commit_all(target, options.commit_message)


@contextmanager
def _worktree(
    git: GitCli, cwd: Path, repository: Path, options: GitOptions, branch: str, base: str
) -> Iterator[Path]:
    root = (cwd / options.root_path).resolve()
    if not root.is_relative_to(cwd.resolve()):
        raise ValueError(f"worktrees root {root} must be inside {cwd}")
    target = root / branch
    git.add_worktree(repository, target, branch, base)
    try:
        for hook in options.hooks_at(LoopHookPoint.WORKTREE_READY):
            git.run_hook(hook, target, repository, target)
    except BaseException as error:
        try:
            git.remove_worktree(repository, target, force=True)
        except Exception as removal:
            error.add_note(f"forced worktree removal also failed: {removal}")
        raise
    try:
        yield target
        for hook in options.hooks_at(LoopHookPoint.WORKTREE_REMOVING):
            git.run_hook(hook, target, repository, target)
    finally:
        git.remove_worktree(repository, target)
