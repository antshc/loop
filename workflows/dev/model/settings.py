"""Per-run settings that the command line decides and the slices read."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loop import LoopHook


@dataclass(frozen=True)
class DevSettings:
    """One dev run's settings; `log_dir` also holds agents' evidence, outside every worktree."""

    log_dir: Path = Path(".loop")
    spec_number: int | None = None
    max_ticket_failures: int = 2
    loop_hooks: tuple[LoopHook, ...] = ()
    visualize: bool = False
    otlp_endpoint: str | None = None

    @property
    def evidence_root(self) -> Path:
        return self.log_dir / "evidence"
