from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

DEFAULT_LOOP_HOOK_TIMEOUT_SEC = 120.0


class LoopHookPoint(StrEnum):
    """Moment in a Loop run at which a Loop hook runs."""

    WORKTREE_READY = "worktree-ready"
    WORKTREE_REMOVING = "worktree-removing"
    RUN_FINISHED = "run-finished"


@dataclass(frozen=True)
class LoopHook:
    """Base of shell commands run by Loop at one fixed `point`. Use a subclass."""

    command: str
    timeout_sec: float = DEFAULT_LOOP_HOOK_TIMEOUT_SEC
    point: ClassVar[LoopHookPoint]

    def __post_init__(self) -> None:
        if not hasattr(type(self), "point"):
            raise TypeError(f"{type(self).__name__} fixes no hook point; use a point subclass")
        if not self.command:
            raise ValueError("Loop hook command must not be empty")
        if self.timeout_sec <= 0:
            raise ValueError("Loop hook timeout_sec must be positive")


class WorktreeReadyLoopHook(LoopHook):
    """Runs in a fresh worktree before the agent starts; may write only gitignored paths."""

    point = LoopHookPoint.WORKTREE_READY


class WorktreeRemovingLoopHook(LoopHook):
    """Runs in the worktree after the safety-net commit and before its removal, on success and failure."""

    point = LoopHookPoint.WORKTREE_REMOVING


class RunFinishedLoopHook(LoopHook):
    """Runs in the repository after the worktree is removed and merged; only when the run succeeded."""

    point = LoopHookPoint.RUN_FINISHED


class LoopHookError(Exception):
    def __init__(self, point: LoopHookPoint, command: str, output: str) -> None:
        super().__init__(f"{point.value} hook failed: {command}\n{output}")
        self.point = point
        self.command = command
        self.output = output
