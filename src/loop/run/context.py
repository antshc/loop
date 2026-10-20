from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..hooks import AgentCliHook


class AgentStartMode(str, Enum):
    """Where the CLI process starts: in the worktree itself, or in the harness with the worktree granted."""

    WORKTREE = "worktree"
    HARNESS = "harness"


@dataclass(frozen=True)
class AgentContext:
    """Agent-level settings shared by every CLI."""

    cwd: Path = field(default_factory=Path.cwd)
    docker_image: str | None = None
    # Extra directories the agent may access (`--add-dir`); the cwd is always included by the CLI.
    add_dirs: tuple[Path, ...] = ()
    start_mode: AgentStartMode = AgentStartMode.WORKTREE
    # The harness checkout the CLI starts in; required by the harness start mode.
    harness_dir: Path | None = None

    def __post_init__(self) -> None:
        # Accepts the mode's string value; an unknown value raises ValueError.
        object.__setattr__(self, "start_mode", AgentStartMode(self.start_mode))
        if self.start_mode is AgentStartMode.HARNESS and self.harness_dir is None:
            raise ValueError("start_mode 'harness' requires harness_dir")


@dataclass(frozen=True)
class AgentOptions:
    """Optional caller overrides; unset (None) fields keep the AgentContext defaults."""

    docker_image: str | None = None
    # Runs stub adapters that only log to the console: no git, Docker or CLI process.
    dry_run: bool = False
    start_mode: AgentStartMode | None = None
    harness_dir: Path | None = None


@dataclass(frozen=True)
class RunContext:
    agent: AgentContext = field(default_factory=AgentContext)
    agent_cli_hooks: tuple[AgentCliHook, ...] = ()
