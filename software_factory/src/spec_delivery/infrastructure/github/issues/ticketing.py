"""Planner-side Ticket creation: create each Ticket, order it with native blocked-by edges, then link it to its Spec."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from loop.shared.process import CommandError

from .client import Issues
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


class SpecTicketingService:
    """Creates a Spec's planned Tickets idempotently; a Ticket is linked to its Spec only after its blocked-by edges exist."""

    def __init__(self, issues: Issues) -> None:
        self._issues = issues

    def create(
        self, spec: SpecGraphSnapshot, drafts: Sequence[TicketDraft]
    ) -> TicketedSpec:
        """Finds or creates each draft's Ticket, adds its blocked-by edges, then links it; a retry repeats none of that."""
        tickets: list[Ticket] = []
        edges: list[BlockedByRelation] = []
        try:
            # Read once, before this call's writes, so a retry still sees what an earlier attempt left behind.
            existing = self._issues.find_tickets(spec)
            linked = self._issues.sub_issue_numbers(spec)
            for draft in drafts:
                ticket = existing.get(draft.key) or self._issues.create_ticket(
                    spec, draft.key, draft.title, draft.body
                )
                for index in draft.blocked_by:
                    self._issues.add_blocked_by(ticket, tickets[index])
                    edges.append(BlockedByRelation(ticket.ref, tickets[index].ref))
                self._issues.link_ticket(spec, ticket, linked)
                linked |= {ticket.number}
                tickets.append(ticket)
        except (CommandError, IssueError) as exception:
            made = ", ".join(f"#{ticket.number}" for ticket in tickets) or "none"
            raise TicketingError(
                f"creating Tickets failed after creating {made}: {exception}",
                tuple(tickets),
            ) from exception
        return TicketedSpec(tuple(tickets), tuple(edges))
