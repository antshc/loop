"""The Event of running a repository's check command on a worktree."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.runtime import Event


@dataclass(frozen=True)
class ChecksPassed(Event):
    """The repository's check command succeeded on the worktree."""
