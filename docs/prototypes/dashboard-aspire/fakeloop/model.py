"""Plain value types shared by the runner and fake agents."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

PhaseKind = Literal["engineer", "code", "agent"]


def new_id(length: int = 12) -> str:
    """Random lowercase hex id."""
    return secrets.token_hex(length)[:length]


@dataclass(frozen=True)
class PhaseParams:
    """What a phase is, declared once when it is entered."""

    name: str
    kind: PhaseKind
    owner: str
    description: str
    retries: int = 0

    @property
    def role(self) -> str:
        """`code` for code phases (commit, push, GitHub reads); otherwise the owner's role."""
        return "code" if self.kind == "code" else self.owner


@dataclass
class Phase:
    """A phase as it runs; `status` stays `running` until the span ends."""

    params: PhaseParams
    status: str = "running"
    attempt: int = 0


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


class GateFailure(Exception):
    """Every attempt failed its gate."""


@dataclass(frozen=True)
class ToolStep:
    """One scripted tool call."""

    tool: str
    args: dict[str, str]
    result: str
    ok: bool = True
    seconds: float = 0.6


@dataclass(frozen=True)
class Attempt:
    """One send to the agent: the tool calls it makes, its answer, and the gate evidence."""

    steps: tuple[ToolStep, ...]
    envelope: dict[str, object]
    checks: tuple[GateCheck, ...]
