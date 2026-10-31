"""The Events of refining a pull request's change set."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Event


@dataclass(frozen=True)
class ChoreRefined(Event):
    """The chore agent's accepted refinement; `committed` is false when it changed nothing and so made no commit."""

    summary: str
    out_of_scope: tuple[str, ...]
    commit_message: str
    committed: bool


@dataclass(frozen=True)
class ChoreDiscarded(Event):
    """The refinement was thrown away and the worktree restored; the delivered branch stands as it was."""

    reason: str
    out_of_scope: tuple[str, ...] = ()
