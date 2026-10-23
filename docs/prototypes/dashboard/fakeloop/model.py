"""Plain value types shared by the tracer, runner, and fake agents."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

PhaseKind = Literal["engineer", "code", "agent"]


def new_id(length: int = 12) -> str:
    """Random lowercase hex id."""
    return secrets.token_hex(length)[:length]


def now_iso() -> str:
    """UTC timestamp in the ISO form the dashboard parses."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class PhaseParams:
    """What a phase is, declared once when it is entered."""

    name: str
    kind: PhaseKind
    owner: str
    description: str
    retries: int = 0


@dataclass
class Phase:
    """A phase as it runs; `status` stays `fail` until a clean exit earns success."""

    phase_id: str
    adw_id: str
    seq: int
    params: PhaseParams
    status: str = "running"
    attempt: int = 0
    error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


@dataclass(frozen=True)
class EventRecord:
    """One row of the events table."""

    adw_id: str
    type: str
    name: str | None = None
    phase_id: str | None = None
    parent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    tokens: int | None = None
    started_at: str | None = None
    ended_at: str | None = None


@dataclass(frozen=True)
class GateCheck:
    """One item a gate inspected."""

    item: str
    ok: bool
    note: str = ""


@dataclass(frozen=True)
class GateReport:
    """A gate verdict with the evidence behind it."""

    checks: tuple[GateCheck, ...]

    @property
    def violations(self) -> list[str]:
        """Notes of the failed checks."""
        return [f"{check.item}: {check.note}" for check in self.checks if not check.ok]

    @property
    def passed(self) -> bool:
        """True when every check held."""
        return all(check.ok for check in self.checks)


@dataclass(frozen=True)
class AgentConfig:
    """A fake agent's lane label and color."""

    name: str
    model: str
    color: str
    coding_agent: str = "copilot"
    context_window: int = 200_000
