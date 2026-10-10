from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..hooks import LoopHook, LoopHookPoint
from .strategies import GitStrategy, HeadStrategy


@dataclass(frozen=True)
class GitOptions:
    """Where the worktree lives (target = root_path/branch) and which strategy creates it."""

    # Must resolve inside the agent cwd; relative paths are taken from the cwd.
    root_path: Path = Path(".worktrees")
    # Repository that git -C targets (multi-repository setups); None uses the agent cwd.
    repository_path: Path | None = None
    strategy: GitStrategy = HeadStrategy()
    # Message for the safety-net commit of changes the agent left uncommitted.
    commit_message: str = "agent: commit uncommitted changes"
    # Loop hooks of any point; each runs at its own point, in declared order.
    loop_hooks: tuple[LoopHook, ...] = ()

    def __post_init__(self) -> None:
        if self.loop_hooks and isinstance(self.strategy, HeadStrategy):
            raise ValueError("Loop hooks need a worktree strategy")

    def hooks_at(self, point: LoopHookPoint) -> tuple[LoopHook, ...]:
        return tuple(hook for hook in self.loop_hooks if hook.point is point)
