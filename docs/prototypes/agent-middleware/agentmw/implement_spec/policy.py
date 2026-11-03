"""implement_spec's policy as values and pure functions, ported from the software factory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .messages import ProposedTicket, Reviewed

# The label that hands a Spec or Ticket to a person.
HITL = "hitl"

# Prefixes every comment the Workflow writes on the tracker.
SOURCE = "implement_spec"

MAX_PLANNED_TICKETS = 15

FALLBACK_CHORE_SUMMARY = "refine the change set"

SUBJECT_LIMIT = 100

REQUIRED_AXES = frozenset({"bugs", "quality", "smells", "initiative"})

# Findings of these kinds keep the pull request a draft; the others are only reported.
BLOCKING_FINDING_KINDS = frozenset({"bug", "initiative"})


@dataclass(frozen=True)
class PlanPolicy:
    """How many Tickets one plan may propose."""

    max_tickets: int = MAX_PLANNED_TICKETS


def ticket_subject_prefix(initiative: str, ticket: int) -> str:
    """The prefix of the one commit that delivers Ticket `ticket`."""
    return f"ccode({initiative}|{ticket}): "


def initiative_prefix(initiative: str) -> str:
    """The prefix every Ticket commit of the Initiative shares."""
    return f"ccode({initiative}|"


def plan_violation(
    tickets: Sequence[ProposedTicket], max_tickets: int = MAX_PLANNED_TICKETS
) -> str | None:
    """Why a plan cannot become Tickets, or None when it can."""
    if not tickets:
        return "the planner returned no Tickets"
    if len(tickets) > max_tickets:
        return (
            f"the planner returned {len(tickets)} Tickets; the limit is {max_tickets}"
        )
    for index, ticket in enumerate(tickets):
        if not ticket.title.strip():
            return f"planned Ticket {index} has no title"
        if any(not 0 <= blocker < index for blocker in ticket.blocked_by):
            return f"planned Ticket {index} is blocked by a Ticket that does not come before it: {list(ticket.blocked_by)}"
    return None


def chore_subject(initiative: str, proposed: str) -> str:
    """`chore(<initiative>): ` + the first line of `proposed` (or a fallback), capped at 100 characters."""
    prefix = f"chore({initiative}): "
    lines = proposed.strip().splitlines()
    line = lines[0].strip() if lines else ""
    line = line.removeprefix(prefix).strip() or FALLBACK_CHORE_SUMMARY
    return f"{prefix}{line}"[:SUBJECT_LIMIT].rstrip()


def review_blockers(review: Reviewed | str | None, head: str) -> list[str]:
    """Why the review keeps the pull request a draft; a string is the reason it failed, None that it did not run."""
    if review is None:
        return ["the review did not run"]
    if isinstance(review, str):
        return [f"the review failed: {review}"]
    blockers: list[str] = []
    if {axis.name for axis in review.axes} != REQUIRED_AXES or any(
        axis.outcome != "completed" for axis in review.axes
    ):
        blockers.append("a review axis did not complete")
    if review.head != head:
        blockers.append(
            f"the review covered {review.head or 'an unknown head'}, not {head}"
        )
    unresolved = [
        finding for finding in review.findings if finding.kind in BLOCKING_FINDING_KINDS
    ]
    if unresolved:
        blockers.append(f"{len(unresolved)} unresolved bug/initiative finding(s)")
    return blockers
