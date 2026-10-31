"""What the engine passes between the interpreter, the attempt and the scope: statuses, outcomes and recorded Events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..event import Event
from ..routes import Route


@dataclass(frozen=True)
class Recorded:
    """An Event a scope holds, with the Stage that produced it."""

    event: Event
    producer: str


@dataclass(frozen=True)
class ScopeStatus:
    """How a scope ended; `next_item` and `rerouted` only travel up to the loop item or dispatch case they address."""

    kind: Literal[
        "completed",
        "idle",
        "held",
        "handed_off",
        "failed",
        "interrupted",
        "next_item",
        "rerouted",
    ]
    reason: str | None = None
    variant: type[Event] | None = None


@dataclass(frozen=True)
class Again:
    """The route of a failed attempt with rework budget left: run the same Stage again."""


@dataclass(frozen=True)
class Outcome:
    """What one Stage placement did: how it ended, the Event it carries and the route to take next."""

    kind: Literal["passed", "failed", "held", "skipped"]
    route: Route | Again
    event: Event | None = None
    reason: str | None = None
