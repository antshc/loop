"""Per-run settings that the command line decides and the implement_spec definition and intake read."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loop import LoopHook

from software_factory.events.specs import RepositoryTarget


@dataclass(frozen=True)
class Targets:
    """The repositories Specs can be delivered in; a service the entry point or a test passes."""

    repositories: tuple[RepositoryTarget, ...]


@dataclass(frozen=True)
class ImplementSpecSettings:
    """One implement_spec invocation's settings; `log_dir` holds the log file and the Execution ledger, outside every worktree."""

    log_dir: Path = Path(".loop")
    spec_number: int | None = None
    max_failures: int = 2
    loop_hooks: tuple[LoopHook, ...] = ()
    otlp_endpoint: str | None = None

    @property
    def ledger_root(self) -> Path:
        """Where every Spec's Execution ledger lives."""
        return self.log_dir / "ledger"
