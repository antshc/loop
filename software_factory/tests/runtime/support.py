"""A small Event and Stage vocabulary the definition tests build Workflows from."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from loop import AgentWorkspace

from software_factory.agents.roles.developer import DEVELOPER
from software_factory.runtime import (
    All, Event, History, Item, Selector, Stage, WorkspaceOpener,
)


@dataclass(frozen=True)
class Spec:
    """The Workflow context."""

    number: int


@dataclass(frozen=True)
class Ticket:
    """The loop item."""

    number: int


class Client:
    """A service type."""


@dataclass(frozen=True)
class Routed(Event):
    target: str


@dataclass(frozen=True)
class Delivered(Event):
    commit: str


@dataclass(frozen=True)
class Published(Event):
    url: str


@dataclass(frozen=True)
class Chored(Event):
    note: str


@dataclass(frozen=True)
class Discarded(Event):
    reason: str


@dataclass(frozen=True)
class Alpha(Event):
    pass


@dataclass(frozen=True)
class Beta(Event):
    pass


@dataclass(frozen=True)
class Gamma(Event):
    pass


type Outcome = Alpha | Beta
type Triple = Alpha | Beta | Gamma


@dataclass(frozen=True)
class NoInput:
    pass


@dataclass(frozen=True)
class NeedsRouted:
    routed: Routed


@dataclass(frozen=True)
class NeedsOptionalRouted:
    routed: Routed | None


@dataclass(frozen=True)
class NeedsAllRouted:
    routed: All[Routed]


@dataclass(frozen=True)
class NeedsHistory:
    past: History[Routed]


@dataclass(frozen=True)
class NeedsPublished:
    published: Published


@dataclass(frozen=True)
class NeedsDelivered:
    delivered: Delivered


@dataclass(frozen=True)
class NeedsOptionalDelivered:
    delivered: Delivered | None


@dataclass(frozen=True)
class NeedsChore:
    chore: Chored | Discarded | None


@dataclass(frozen=True)
class NeedsSpec:
    spec: Spec


@dataclass(frozen=True)
class NeedsTicket:
    ticket: Ticket


@dataclass(frozen=True)
class NeedsWorkspace:
    workspace: AgentWorkspace


@dataclass(frozen=True)
class NeedsClient:
    client: Client


@dataclass(frozen=True)
class NeedsOutcome:
    outcome: Outcome


@dataclass(frozen=True)
class NeedsAlpha:
    alpha: Alpha


@dataclass(frozen=True)
class PrimitiveInput:
    count: int


class NotADataclass:
    pass


@dataclass
class Unfrozen:
    routed: Routed


def make(id: str, input: type = NoInput, produces: Any = Routed) -> Stage[Any, Any, Any]:
    """A pure gate-only Stage."""
    return Stage(id, input, produces)


def effect(id: str, input: type = NoInput, produces: Any = Routed) -> Stage[Any, Any, Any]:
    """An effect Stage with a reconcile."""
    return Stage(id, input, produces).effect(lambda _: None, reconcile=lambda _: None)


def agent(id: str, input: type = NoInput, produces: Any = Routed) -> Stage[Any, Any, Any]:
    """An agent Stage."""
    return Stage(id, input, produces).agent(DEVELOPER, lambda *_: None)


def tickets(id: str = "next-ticket") -> Selector[Any, Ticket]:
    """A Selector of Tickets."""
    return Selector(id, NoInput, Ticket, lambda _: [Item(Ticket(1), "1", "r1")])


def checkout(id: str = "checkout") -> WorkspaceOpener[Any]:
    """A workspace opener."""
    return WorkspaceOpener(id, NoInput, lambda _: nullcontext())
