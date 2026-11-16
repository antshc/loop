"""Workspace openers (the harness checkout, the feature worktree and a pull request's recovery worktree an agent Stage runs in) and the clean-tree check."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loop import (
    AgentWorkspace,
    BranchStrategy,
    Evidence,
    GitOptions,
    Nothing,
    Pass,
    Stage,
    Verdict,
    WorkspaceOpener,
    WorktreeError,
)
from loop.shared.git import GitClient
from loop.software_factory.infrastructure.agents.launcher import (
    AgentLauncher,
)

from software_factory.src.pull_requests.events.pull_requests import PullRequestPolled

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PullRequestWorktreeInput:
    polled: PullRequestPolled
    launcher: AgentLauncher
    git: GitClient


@dataclass(frozen=True)
class RemoveWorktreeInput:
    polled: PullRequestPolled
    git: GitClient


def pr_worktree() -> WorkspaceOpener[PullRequestWorktreeInput]:
    """The pull request's recovery worktree on the pull request's head branch name, at the head the batch opens at, kept across polls and runs until `remove_worktree`; commits an earlier run kept ahead of that head stay, a branch left behind is advanced to it, a stale pre-rebase copy of it is reset to it, any other diverged one raises WorktreeError."""
    return WorkspaceOpener(
        "pr-worktree", PullRequestWorktreeInput, _open_pull_request_worktree
    )


@contextmanager
def _open_pull_request_worktree(
    i: PullRequestWorktreeInput,
) -> Iterator[AgentWorkspace]:
    target, head = i.polled.target, i.polled.head
    i.git.fetch_revision(target.checkout, head)
    options = GitOptions(
        root_path=target.worktrees,
        repository_path=target.checkout,
        strategy=BranchStrategy(i.polled.branch, head, keep=True),
    )
    with i.launcher().with_git(options).open() as workspace:
        _align(i.git, workspace.path, head)
        yield workspace


def remove_worktree() -> Stage[RemoveWorktreeInput, None, Nothing]:
    """Removes the pull request's kept worktree once the pull request is closed; a missing one is fine, a dirty one is kept for inspection. The branch stays."""
    return (
        Stage("remove-worktree", RemoveWorktreeInput, produces=Nothing)
        .effect(_remove_worktree, reconcile=lambda _: None)
        .gate(_removed)
    )


def _remove_worktree(i: RemoveWorktreeInput) -> None:
    target = i.polled.target
    path = target.worktrees / i.polled.branch
    if not path.is_dir():
        return
    if not i.git.is_clean(path):
        logger.warning("dirty worktree kept after close: %s", path)
        return
    logger.info("removing worktree %s", path)
    i.git.text(target.checkout, "worktree", "remove", str(path))


def _removed(_: RemoveWorktreeInput, __: None, ___: Evidence) -> Verdict[Nothing]:
    return Pass(Nothing())


def _align(git: GitClient, path: Path, head: str) -> None:
    """Keeps commits ahead of the pull request head, advances a branch left behind it, resets a stale pre-rebase copy to it and refuses any other diverged one."""
    actual = git.head(path).sha
    if git.is_ancestor(path, head, actual):
        return
    if git.is_ancestor(path, actual, head):
        git.advance(path, head)
    elif git.is_clean(path) and git.is_rebased_copy(path, head):
        # The pull request was rebased or force-pushed: every local commit is already on the new head.
        git.restore(path, head)
    else:
        raise WorktreeError(
            f"Unexpected local commits; inspect recovery branch at {path}"
        )
