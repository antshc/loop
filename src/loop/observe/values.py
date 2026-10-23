from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol


@dataclass(frozen=True)
class Usage:
    """Model usage of one Agent Run turn."""

    api_calls: int = 0
    # Uncached input only; cache reads and writes are counted apart.
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # GitHub AI units per token type.
    aiu: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    premium_requests: float = 0.0

    @property
    def total_input_tokens(self) -> int:
        """Input tokens as the OpenTelemetry GenAI conventions count them: uncached plus cache reads and writes."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_aiu(self) -> float:
        """AI units over every token type."""
        return sum(self.aiu.values())


@dataclass(frozen=True)
class ToolCall:
    """One tool call with the real start and end the CLI logged."""

    call_id: str
    name: str
    arguments: Mapping[str, object]
    result: str
    ok: bool
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True)
class LogReading:
    """What a CLI's own session log says about one turn."""

    native_id: str
    model: str | None
    reasoning_effort: str | None
    usage: Usage
    tools: tuple[ToolCall, ...]


@dataclass(frozen=True)
class AgentRunRecord:
    """One finished Agent Run turn; `usage` None or `tools` empty means unobservable, never zero."""

    cli: str
    session: str
    native_id: str | None
    cwd: Path
    started_at: datetime
    ended_at: datetime
    # None when the runner raised; `error` then holds the exception.
    exit_code: int | None
    error: str | None
    model: str | None
    reasoning_effort: str | None
    usage: Usage | None
    tools: tuple[ToolCall, ...]
    prompt: str
    output: str | None
    # The profile's name; None for an unnamed agent.
    agent: str | None = None


@dataclass(frozen=True)
class WorktreeEvent:
    """A Worktree lifecycle moment; `ok` is None until the Worktree is removing."""

    point: Literal["ready", "removing", "removed"]
    path: Path
    at: datetime
    ok: bool | None


class RunObserver(Protocol):
    """Receives facts about Agent Runs and Worktrees synchronously on the run's thread."""

    def agent_run(self, record: AgentRunRecord) -> None: ...

    def worktree(self, event: WorktreeEvent) -> None: ...
