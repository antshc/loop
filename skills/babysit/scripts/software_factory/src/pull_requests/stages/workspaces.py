"""Workspace openers (the harness checkout, the feature worktree and a pull request's recovery worktree an agent Stage runs in) and the clean-tree check."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loop import (
    AgentWorkspace,
    BranchStrategy,
    GitOptions,
    WorkspaceOpener,
    WorktreeError,
)
from loop.shared.git import GitClient
from loop.software_factory.infrastructure.agents.launcher import (
    AgentLauncher,
)

from software_factory.src.pull_requests.events.pull_requests import PullRequestPolled


@dataclass(frozen=True)
class PullRequestWorktreeInput:
    polled: PullRequestPolled
    launcher: AgentLauncher
    git: GitClient


def pr_worktree() -> WorkspaceOpener[PullRequestWorktreeInput]:
    """The pull request's recovery worktree on `babysit/<repository>/<number>`, at the head the batch opens at; commits an earlier run kept ahead of that head stay, a branch left behind is advanced to it, a stale pre-rebase copy of it is reset to it, any other diverged one raises WorktreeError."""
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
        strategy=BranchStrategy(f"babysit/{target.slug}/{i.polled.number}", head),
    )
    with i.launcher().with_git(options).open() as workspace:
        _align(i.git, workspace.path, head)
        yield workspace


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
