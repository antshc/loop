from __future__ import annotations

import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from ..sessions import Turn


class AgentCliHookPoint(StrEnum):
    """Moment in a CLI run at which a native hook fires."""

    SESSION_START = "session-start"
    SESSION_END = "session-end"
    AGENT_STOP = "agent-stop"
    PROMPT_SUBMITTED = "prompt-submitted"
    PRE_TOOL = "pre-tool"
    POST_TOOL = "post-tool"
    SUBAGENT_START = "subagent-start"
    SUBAGENT_STOP = "subagent-stop"
    PRE_COMPACT = "pre-compact"


@dataclass(frozen=True)
class AgentCliHook:
    """Base of shell commands run by the CLI's native hook at one fixed `point`; observe-only. Use a subclass."""

    command: str
    timeout_sec: int = 30
    point: ClassVar[AgentCliHookPoint]

    def __post_init__(self) -> None:
        if not hasattr(type(self), "point"):
            raise TypeError(f"{type(self).__name__} fixes no hook point; use a point subclass")
        if not self.command:
            raise ValueError("hook command must not be empty")
        if self.timeout_sec <= 0:
            raise ValueError("hook timeout_sec must be positive")


class SessionStartAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SESSION_START


class SessionEndAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SESSION_END


class AgentStopAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.AGENT_STOP


class PromptSubmittedAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.PROMPT_SUBMITTED


class PreToolAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.PRE_TOOL


class PostToolAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.POST_TOOL


class SubagentStartAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SUBAGENT_START


class SubagentStopAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SUBAGENT_STOP


class PreCompactAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.PRE_COMPACT


class UnsupportedAgentCliHookPoint(Exception):
    """A requested hook point cannot fire in the CLI."""


@dataclass(frozen=True)
class AgentCliHookWiring:
    """What the runner applies around one CLI process; `files` paths are relative to the agent cwd."""

    args: tuple[str, ...] = ()
    files: Mapping[Path, str] = field(default_factory=dict)
    git_excludes: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


_SHIM = Path(__file__).with_name("shim.py").resolve()


def ordered(hooks: tuple[AgentCliHook, ...]) -> list[tuple[AgentCliHookPoint, AgentCliHook]]:
    """(point, hook) pairs in point-enum order, then declaration order."""
    return [(point, hook) for point in AgentCliHookPoint for hook in hooks if hook.point is point]


def shim_command(cli: str, point: AgentCliHookPoint, turn: Turn, hook: AgentCliHook) -> str:
    """Shell command that runs the shim, which in turn runs the user's `hook.command`."""
    return shlex.join(
        [sys.executable, str(_SHIM), "--cli", cli, "--point", point.value, "--session", turn.name.value, "--", hook.command]
    )
