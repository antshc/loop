"""Pure evaluation of whether a Ticket's native blocked-by dependencies let it be delivered."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from .issue import CloseReason, IssueState


if TYPE_CHECKING:
    from .graph import BlockedByRelation, SpecGraphSnapshot


class TicketReadiness(StrEnum):
    """READY: every blocker is done. BLOCKED: a blocker is open or was dropped. UNKNOWN: the dependencies were not fully read."""

    READY = "ready"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


# A blocker closed this way was abandoned, not delivered, so a human decides what happens to its dependents.
_UNSATISFYING_REASONS = frozenset({CloseReason.NOT_PLANNED, CloseReason.DUPLICATE})


def _satisfied(relation: BlockedByRelation) -> bool:
    return relation.state is IssueState.CLOSED and relation.close_reason not in _UNSATISFYING_REASONS


class TicketReadinessPolicy:
    """Evaluates each Ticket of a Spec from its native blocked-by edges alone; "Blocked by #N" text in a body is never read."""

    def evaluate(self, graph: SpecGraphSnapshot) -> dict[int, TicketReadiness]:
        """The readiness of every Ticket of `graph`, by Ticket number; whether a Ticket is actionable is a separate question."""
        return {ticket.number: self._readiness(graph, ticket.number) for ticket in graph.tickets}

    @staticmethod
    def _readiness(graph: SpecGraphSnapshot, number: int) -> TicketReadiness:
        if number in graph.unread_dependencies:
            return TicketReadiness.UNKNOWN
        blockers = [relation for relation in graph.blocked_by if relation.ticket.number == number]
        return TicketReadiness.READY if all(_satisfied(relation) for relation in blockers) else TicketReadiness.BLOCKED
