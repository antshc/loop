"""implement_spec's delivery policy over the tracker's commands: `hitl` hand-off, escalation, and closing a delivered Ticket."""

from __future__ import annotations

from software_factory.shared.github.issues.graph import SpecGraphSnapshot

from .records import HITL_LABEL
from .repositories import Tracker


def hand_to_human(tracker: Tracker, spec: SpecGraphSnapshot, message: str) -> SpecGraphSnapshot:
    """Labels the Spec `hitl` and comments `message` on it."""
    tracker.add_label(spec.number, HITL_LABEL)
    tracker.comment(spec.number, "hand-off", message)
    return spec.labeled(HITL_LABEL)


def block(tracker: Tracker, spec: SpecGraphSnapshot, message: str) -> SpecGraphSnapshot:
    """Hands the Spec to a human, but only when there is work it is blocking."""
    return hand_to_human(tracker, spec, message) if spec.has_work else spec


def escalate(tracker: Tracker, spec: SpecGraphSnapshot, ticket_number: int, reason: str) -> SpecGraphSnapshot:
    """Hands both the Ticket and the Spec to a human."""
    spec.ticket(ticket_number)
    message = f"implement_spec: {reason}"
    tracker.add_label(ticket_number, HITL_LABEL)
    tracker.comment(ticket_number, "escalation", message)
    return hand_to_human(tracker, spec.ticket_labeled(ticket_number, HITL_LABEL), message)


def close_ticket(tracker: Tracker, spec: SpecGraphSnapshot, ticket_number: int, comment: str) -> SpecGraphSnapshot:
    """Closes a delivered Ticket with `comment`."""
    spec.ticket(ticket_number)
    tracker.close(ticket_number, comment)
    return spec.ticket_closed(ticket_number)


def announce_delivered(tracker: Tracker, spec: SpecGraphSnapshot, pull_request_url: str) -> None:
    """Tells the Spec that all its Tickets are delivered and where the draft PR is."""
    tracker.comment(spec.number, "delivered", f"implement_spec: all Tickets delivered; draft pull request: {pull_request_url}")
