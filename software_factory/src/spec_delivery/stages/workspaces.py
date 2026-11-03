"""The specification delivery worktree opener."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from loop import BranchStrategy, GitOptions, LoopHook, WorkspaceOpener
from loop.software_factory.infrastructure.agents.launcher import AgentLauncher

from software_factory.src.spec_delivery.events.specs import SpecRouted


@dataclass(frozen=True)
class FeatureWorktreeInput:
    routed: SpecRouted
    launcher: AgentLauncher


def feature_worktree(
    *, hooks: Sequence[LoopHook] = ()
) -> WorkspaceOpener[FeatureWorktreeInput]:
    """Open a Spec feature branch worktree from the configured base branch."""

    def open_worktree(i: FeatureWorktreeInput):
        target = i.routed.target
        options = GitOptions(
            root_path=target.worktrees,
            repository_path=target.checkout,
            strategy=BranchStrategy(
                i.routed.feature_branch, f"origin/{i.routed.base_branch}"
            ),
            loop_hooks=tuple(hooks),
        )
        return i.launcher().with_git(options).open()

    return WorkspaceOpener("feature-worktree", FeatureWorktreeInput, open_worktree)
