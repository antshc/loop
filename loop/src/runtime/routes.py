"""Routes: where control goes after a Stage ends in pass, fail or hold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .event import Event
    from .stage import Stage


@dataclass(frozen=True)
class Next:
    """Continue with the next node, or leave an `each` loop when used as its on_fail or on_hold."""


@dataclass(frozen=True)
class NextItem:
    """Inside an `each` body only: end this item successfully and select the next."""


@dataclass(frozen=True)
class Stop:
    """End the scope with a terminal status."""

    status: Literal["completed", "idle", "held", "failed"]
    reason: str | None = None


@dataclass(frozen=True)
class Handoff:
    """Run `stage` (an effect or action Stage, never an agent), then end the scope handed off."""

    stage: Stage[Any, Any, Any]


@dataclass(frozen=True)
class Rework:
    """Re-run this same Stage until `max_attempts` attempts have started, then take `then`."""

    max_attempts: int
    then: Stop | Handoff


@dataclass(frozen=True)
class Reroute:
    """Inside a `dispatch` case only: abandon this case and run sibling case `variant`."""

    variant: type[Event]


type Route = Next | NextItem | Rework | Stop | Handoff | Reroute


def next_() -> Next:
    """The route that continues with the next node."""
    return Next()


def next_item() -> NextItem:
    """The route that ends the current `each` item successfully."""
    return NextItem()


def rework(*, max_attempts: int, then: Stop | Handoff | None = None) -> Rework:
    """The route that re-runs the same Stage up to `max_attempts` times, then stops failed unless `then` says otherwise."""
    return Rework(max_attempts, then if then is not None else Stop("failed"))


def handoff(stage: Stage[Any, Any, Any]) -> Handoff:
    """The route that runs an effect or action Stage and then ends the scope handed off."""
    return Handoff(stage)


def reroute(variant: type[Event]) -> Reroute:
    """The route that jumps to the sibling `dispatch` case of `variant`."""
    return Reroute(variant)


class stop:
    """Namespace of the terminal routes."""

    @staticmethod
    def completed() -> Stop:
        """End the scope completed."""
        return Stop("completed")

    @staticmethod
    def idle() -> Stop:
        """End the scope with nothing to do."""
        return Stop("idle")

    @staticmethod
    def held(reason: str | None = None) -> Stop:
        """End the scope held for a person or a later run."""
        return Stop("held", reason)

    @staticmethod
    def failed(reason: str | None = None) -> Stop:
        """End the scope failed."""
        return Stop("failed", reason)


@dataclass(frozen=True)
class Routes:
    """The three outcome routes of a Stage; the defaults are pass to the next node, fail to stop failed, hold to stop held."""

    passed: Route = field(default_factory=Next)
    failed: Route = field(default_factory=lambda: Stop("failed"))
    held: Route = field(default_factory=lambda: Stop("held"))


def is_route(value: object) -> bool:
    """Whether `value` is one of the route values."""
    return isinstance(value, (Next, NextItem, Rework, Stop, Handoff, Reroute))
