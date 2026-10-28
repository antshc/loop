"""Planner-side Ticket creation: create each Ticket, order it with native blocked-by edges, then link it to its Spec."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ...process import CommandError
from .errors import IssueError
from .graph import BlockedByRelation, SpecGraphSnapshot
from .spec import Ticket


@dataclass(frozen=True)
class TicketDraft:
    """One Ticket to create; `key` identifies it across retries and `blocked_by` holds indexes of earlier drafts."""

    key: str
    title: str
    body: str
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class TicketedSpec:
    """The Tickets created for a Spec, in draft order, and the blocked-by edges between them."""

    tickets: tuple[Ticket, ...]
    blocked_by: tuple[BlockedByRelation, ...]


class TicketingError(IssueError):
    """Creating a Spec's Tickets stopped part-way; `created` are the Tickets already made and kept."""

    def __init__(self, message: str, created: tuple[Ticket, ...]) -> None:
        super().__init__(message)
        self.created = created


class TicketWriter(Protocol):
    """The tracker commands Ticket creation needs; each takes effect at once and a retry skips what is already applied."""

    def find_ticket(self, spec: SpecGraphSnapshot, key: str) -> Ticket | None: ...

    def create_ticket(self, spec: SpecGraphSnapshot, key: str, title: str, body: str) -> Ticket: ...

    def link_ticket(self, spec: SpecGraphSnapshot, ticket: Ticket) -> None: ...

    def add_blocked_by(self, ticket: Ticket, blocker: Ticket) -> None: ...


class SpecTicketingService:
    """Creates a Spec's planned Tickets idempotently; a Ticket is linked to its Spec only after its blocked-by edges exist."""

    def __init__(self, writer: TicketWriter) -> None:
        self._writer = writer

    def create(self, spec: SpecGraphSnapshot, drafts: Sequence[TicketDraft]) -> TicketedSpec:
        """Finds or creates each draft's Ticket, adds its blocked-by edges, then links it; a retry repeats none of that."""
        tickets: list[Ticket] = []
        edges: list[BlockedByRelation] = []
        try:
            for draft in drafts:
                ticket = self._writer.find_ticket(spec, draft.key) or self._writer.create_ticket(spec, draft.key, draft.title, draft.body)
                for index in draft.blocked_by:
                    self._writer.add_blocked_by(ticket, tickets[index])
                    edges.append(BlockedByRelation(ticket.ref, tickets[index].ref))
                self._writer.link_ticket(spec, ticket)
                tickets.append(ticket)
        except (CommandError, IssueError) as exception:
            made = ", ".join(f"#{ticket.number}" for ticket in tickets) or "none"
            raise TicketingError(f"creating Tickets failed after creating {made}: {exception}", tuple(tickets)) from exception
        return TicketedSpec(tuple(tickets), tuple(edges))
