"""Workspace openers: the harness checkout and the feature worktree an agent Stage runs in."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from loop import BranchStrategy, GitOptions, LoopHook

from software_factory.events.specs import SpecRouted
from software_factory.runtime import WorkspaceOpener
from software_factory.shared.agents.launcher import AgentLauncher


@dataclass(frozen=True)
class CheckoutInput:
    launcher: AgentLauncher


@dataclass(frozen=True)
class FeatureWorktreeInput:
    routed: SpecRouted
    launcher: AgentLauncher


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
