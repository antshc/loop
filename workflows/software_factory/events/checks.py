"""The Event of running a repository's check command on a worktree."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Event


@dataclass(frozen=True)
class ChecksPassed(Event):
    """The repository's check command succeeded on the worktree."""
