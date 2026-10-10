from __future__ import annotations

from dataclasses import dataclass

from ..sessions import SessionName


@dataclass(frozen=True)
class AgentRequest:
    """One agent execution."""

    prompt: str


@dataclass(frozen=True)
class AgentResult:
    """Final outcome of one execution, carrying the session name it ran under."""

    output: str
    session: SessionName
    exit_code: int
