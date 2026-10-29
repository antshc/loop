"""The Events of selecting, implementing, closing and escalating Tickets and of the delivery they add up to."""

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
    """`ticket` is closed with its delivery comment."""

    ticket: int
    commit: str


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
