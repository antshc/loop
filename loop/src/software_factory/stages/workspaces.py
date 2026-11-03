"""Generic checkout openers and clean-worktree checks."""

from __future__ import annotations

from dataclasses import dataclass

from loop import (
    AgentWorkspace,
    Evidence,
    Hold,
    Nothing,
    Pass,
    Stage,
    Verdict,
    WorkspaceOpener,
)
from loop.shared.git import GitClient
from loop.software_factory.infrastructure.agents.launcher import (
    AgentLauncher,
)


@dataclass(frozen=True)
class CheckoutInput:
    launcher: AgentLauncher


@dataclass(frozen=True)
class CleanInput:
    worktree: AgentWorkspace
    git: GitClient


def checkout() -> WorkspaceOpener[CheckoutInput]:
    """The harness checkout itself: no worktree is created, so read-only Stages run where the harness runs."""
    return WorkspaceOpener("checkout", CheckoutInput, lambda i: i.launcher().open())


def require_clean() -> Stage[CleanInput, bool, Nothing]:
    """Holds when the worktree has any staged, unstaged or untracked change, so no agent edits on top of unrelated work."""
    return (
        Stage("require-clean", CleanInput, produces=Nothing)
        .action(lambda i: i.git.is_clean(i.worktree.path))
        .gate(_clean)
    )


def _clean(_: CleanInput, clean: bool, __: Evidence) -> Verdict[Nothing]:
    return Pass(Nothing()) if clean else Hold("Worktree contains unrelated edits")
