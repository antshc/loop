from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..hooks import AgentCliHook


@dataclass(frozen=True)
class AgentContext:
    """Agent-level settings shared by every CLI."""

    cwd: Path = field(default_factory=Path.cwd)
    docker_image: str | None = None
    # Extra directories the agent may access (`--add-dir`); the cwd is always included by the CLI.
    add_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class AgentOptions:
    """Optional caller overrides; unset (None) fields keep the AgentContext defaults."""

    docker_image: str | None = None
    # Runs stub adapters that only log to the console: no git, Docker or CLI process.
    dry_run: bool = False


@dataclass(frozen=True)
class RunContext:
    agent: AgentContext = field(default_factory=AgentContext)
    agent_cli_hooks: tuple[AgentCliHook, ...] = ()
