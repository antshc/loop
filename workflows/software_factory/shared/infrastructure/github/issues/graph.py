"""A Spec with its Tickets and their relations, loaded together as one snapshot."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from ..identity import IssueRef
from .issue import CloseReason, Comment, IssueState, ReadGap
from .issue_classifier import LabelTypeDisagreement
from .readiness import TicketReadiness, TicketReadinessPolicy
from .spec import HITL_LABEL, Spec, Ticket


@dataclass(frozen=True)
class IssueRelation:
    """A parent/sub-issue edge between two issues."""

    parent: IssueRef
    child: IssueRef


@dataclass(frozen=True)
class BlockedByRelation:
    """A native blocked-by edge: `ticket` waits for `blocker`, whose state and close reason were read with the edge."""

    ticket: IssueRef
    blocker: IssueRef
    state: IssueState = IssueState.OPEN
    close_reason: CloseReason | None = None


@dataclass(frozen=True)
class SpecGraphSnapshot:
    """An immutable Spec, its open sub-issues as Tickets, and their relations; `complete` says whether every read behind it succeeded."""

    spec: Spec
    tickets: tuple[Ticket, ...] = ()
    relations: tuple[IssueRelation, ...] = ()
    # Sub-issues of every state, so a Spec whose Tickets are all closed is not mistaken for an unplanned one.
    sub_issue_total: int = 0
    blocked_by: tuple[BlockedByRelation, ...] = ()
    # Tickets whose blocked-by edges could not be fully read or were denied; their readiness is UNKNOWN, not "no blockers".
    unread_dependencies: tuple[int, ...] = ()
    gaps: tuple[ReadGap, ...] = ()
    disagreement: LabelTypeDisagreement | None = None

    @property
    def complete(self) -> bool:
        """The Spec, every Ticket, and the sub-issue listing were all fully read."""
        return (
            not self.gaps
            and self.spec.complete
            and all(ticket.complete for ticket in self.tickets)
        )

    @property
    def number(self) -> int:
        """The Spec's issue number."""
        return self.spec.number

    @property
    def title(self) -> str:
        """The Spec's title."""
        return self.spec.title

    @property
    def url(self) -> str:
        """The Spec's web address."""
        return self.spec.url

    @property
    def body(self) -> str:
        """The Spec's description."""
        return self.spec.body

    @property
    def labels(self) -> tuple[str, ...]:
        """The Spec's labels."""
        return self.spec.labels

    @property
    def comments(self) -> tuple[Comment, ...]:
        """The Spec's Comments."""
        return self.spec.comments

    @property
    def initiative(self) -> str:
        """The `<initiative>: <title>` title prefix, or the Spec number when absent."""
        return self.spec.metadata.initiative

    @property
    def bare_title(self) -> str:
        """The title without its Initiative prefix."""
        return self.spec.metadata.bare_title

    @property
    def target(self) -> str | None:
        """The single `repo:target:<owner/name>` label's value, or None when missing, malformed, or duplicated."""
        return self.spec.metadata.target

    @property
    def target_label(self) -> str | None:
        """The `repo:target:<owner/name>` label that routes the Spec, or None when `target` is."""
        return self.spec.metadata.target_label

    @property
    def base_branch(self) -> str | None:
        """The single `repo:base:<branch>` label's value, or None when missing, empty, or duplicated."""
        return self.spec.metadata.base_branch

    @property
    def awaiting_human(self) -> bool:
        """Carries the `hitl` label."""
        return HITL_LABEL in self.spec.labels

    @property
    def readiness(self) -> dict[int, TicketReadiness]:
        """Every Ticket's READY, BLOCKED, or UNKNOWN readiness, by Ticket number."""
        return TicketReadinessPolicy().evaluate(self)

    @property
    def held_tickets(self) -> tuple[int, ...]:
        """The open Tickets that cannot be offered: BLOCKED, UNKNOWN, or carrying `hitl` or `spec`."""
        readiness = self.readiness
        return tuple(
            ticket.number
            for ticket in self.tickets
            if ticket.state is IssueState.OPEN
            and not (
                ticket.actionable and readiness[ticket.number] is TicketReadiness.READY
            )
        )

    @property
    def deliverable_tickets(self) -> tuple[Ticket, ...]:
        """The Tickets that are not held, in order: the READY ones and those already delivered."""
        held = self.held_tickets
        return tuple(ticket for ticket in self.tickets if ticket.number not in held)

    @property
    def has_work(self) -> bool:
        """Has at least one actionable, READY Ticket."""
        return any(ticket.actionable for ticket in self.deliverable_tickets)

    @property
    def needs_plan(self) -> bool:
        """Has no sub-issues; never true from an incomplete read, which cannot tell no sub-issues from unread ones."""
        return self.complete and self.sub_issue_total == 0 and not self.tickets

    def ticket(self, number: int) -> Ticket:
        """The Ticket numbered `number`; raises when it does not belong to this Spec."""
        for ticket in self.tickets:
            if ticket.number == number:
                return ticket
        raise ValueError(f"ticket #{number} does not belong to spec #{self.number}")

    def labeled(self, label: str) -> SpecGraphSnapshot:
        """This snapshot with the Spec carrying `label`."""
        return replace(self, spec=Spec.of(self.spec.with_label(label)))

    def ticket_labeled(self, number: int, label: str) -> SpecGraphSnapshot:
        """This snapshot with the Ticket carrying `label`."""
        return self._with_ticket(Ticket.of(self.ticket(number).with_label(label)))

    def ticket_closed(self, number: int) -> SpecGraphSnapshot:
        """This snapshot with the Ticket closed as completed, which satisfies the edges that wait for it."""
        closed = Ticket.of(self.ticket(number).closed(CloseReason.COMPLETED))
        edges = tuple(
            replace(edge, state=IssueState.CLOSED, close_reason=CloseReason.COMPLETED)
            if edge.blocker == closed.ref
            else edge
            for edge in self.blocked_by
        )
        return replace(self._with_ticket(closed), blocked_by=edges)

    def with_created(
        self, created: Iterable[Ticket], blocked_by: Iterable[BlockedByRelation] = ()
    ) -> SpecGraphSnapshot:
        """This snapshot with newly created Tickets appended, related to the Spec, and ordered by `blocked_by`."""
        created = tuple(created)
        relations = tuple(
            IssueRelation(self.spec.ref, ticket.ref) for ticket in created
        )
        return replace(
            self,
            tickets=(*self.tickets, *created),
            relations=(*self.relations, *relations),
            blocked_by=(*self.blocked_by, *blocked_by),
            sub_issue_total=self.sub_issue_total + len(created),
        )

    def _with_ticket(self, changed: Ticket) -> SpecGraphSnapshot:
        return replace(
            self,
            tickets=tuple(
                changed if ticket.number == changed.number else ticket
                for ticket in self.tickets
            ),
        )
