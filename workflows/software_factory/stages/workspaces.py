"""Workspace openers (the harness checkout, the feature worktree and a pull request's recovery worktree an agent Stage runs in) and the clean-tree check."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loop import AgentWorkspace, BranchStrategy, GitOptions, LoopHook, WorktreeError

from workflows.software_factory.events.pull_requests import PullRequestPolled
from workflows.software_factory.events.specs import SpecRouted
from loop import Evidence, Hold, Nothing, Pass, Stage, Verdict, WorkspaceOpener
from workflows.software_factory.infrastructure.agents.launcher import AgentLauncher
from loop.shared.git import GitClient


@dataclass(frozen=True)
class CheckoutInput:
    launcher: AgentLauncher


@dataclass(frozen=True)
class FeatureWorktreeInput:
    routed: SpecRouted
    launcher: AgentLauncher


@dataclass(frozen=True)
class PullRequestWorktreeInput:
    polled: PullRequestPolled
    launcher: AgentLauncher
    git: GitClient


@dataclass(frozen=True)
class CleanInput:
    worktree: AgentWorkspace
    git: GitClient


def checkout() -> WorkspaceOpener[CheckoutInput]:
    """The harness checkout itself: no worktree is created, so read-only Stages run where the harness runs."""
    return WorkspaceOpener("checkout", CheckoutInput, lambda i: i.launcher().open())


def feature_worktree(*, hooks: Sequence[LoopHook] = ()) -> WorkspaceOpener[FeatureWorktreeInput]:
    """A worktree of the routed repository on the Spec's feature branch, started from origin's base branch, with the Loop `hooks`."""
    def open_worktree(i: FeatureWorktreeInput):
        target = i.routed.target
        options = GitOptions(
            root_path=target.worktrees,
            repository_path=target.checkout,
            strategy=BranchStrategy(i.routed.feature_branch, f"origin/{i.routed.base_branch}"),
            loop_hooks=tuple(hooks),
        )
        return i.launcher().with_git(options).open()

    return WorkspaceOpener("feature-worktree", FeatureWorktreeInput, open_worktree)


def pr_worktree() -> WorkspaceOpener[PullRequestWorktreeInput]:
    """The pull request's recovery worktree on `babysit/<repository>/<number>`, at the head the batch opens at; commits an earlier run kept ahead of that head stay, a branch left behind is advanced to it, a stale pre-rebase copy of it is reset to it, any other diverged one raises WorktreeError."""
    return WorkspaceOpener("pr-worktree", PullRequestWorktreeInput, _open_pull_request_worktree)


def require_clean() -> Stage[CleanInput, bool, Nothing]:
    """Holds when the worktree has any staged, unstaged or untracked change, so no agent edits on top of unrelated work."""
    return (
        Stage("require-clean", CleanInput, produces=Nothing)
        .action(lambda i: i.git.is_clean(i.worktree.path))
        .gate(_clean)
    )


@contextmanager
def _open_pull_request_worktree(i: PullRequestWorktreeInput) -> Iterator[AgentWorkspace]:
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
        raise WorktreeError(f"Unexpected local commits; inspect recovery branch at {path}")


def _clean(_: CleanInput, clean: bool, __: Evidence) -> Verdict[Nothing]:
    return Pass(Nothing()) if clean else Hold("Worktree contains unrelated edits")
