"""The Events of planning, creating, selecting, implementing, closing and escalating Tickets and of the delivery they add up to."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.runtime import Event


@dataclass(frozen=True)
class TicketImplemented(Event):
    """The developer delivered `ticket` in exactly one validated commit."""

    ticket: int
    commit: str
    summary: str
    verification: str


@dataclass(frozen=True)
class TicketRejected(Event):
    """Why the developer's attempt at `ticket` was refused, kept as evidence for an escalation."""

    ticket: int
    reason: str


@dataclass(frozen=True)
class TicketClosed(Event):
    """`ticket` is closed with its delivery comment; `title` is kept so a pull request description can list it as delivered."""

    ticket: int
    commit: str
    title: str = ""


@dataclass(frozen=True)
class TicketEscalated(Event):
    """`ticket` and its Spec carry `label` and a comment with the reason a person is needed."""

    ticket: int
    label: str
    reason: str


@dataclass(frozen=True)
class Delivery(Event):
    """Every Ticket is delivered: the feature branch head and its commits since the base branch, oldest first."""

    head: str
    commits: tuple[str, ...]


@dataclass(frozen=True)
class PlannedTicket:
    """One Ticket of a plan; `blocked_by` holds zero-based indexes of earlier planned Tickets."""

    title: str
    body: str
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlanProposed(Event):
    """The planner's valid plan: the Tickets in dependency order."""

    tickets: tuple[PlannedTicket, ...]


@dataclass(frozen=True)
class TicketsCreated(Event):
    """The plan is persisted on the Spec and every planned Ticket exists, linked, with its blocked-by edges."""

    tickets: tuple[int, ...]
