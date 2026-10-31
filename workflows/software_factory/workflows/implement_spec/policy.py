"""implement_spec's policy as values and pure functions: the hand-off label and texts, commit subjects, the work item and when held work is released."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from workflows.software_factory.events.specs import SpecBlocked, SpecHandedToHuman, SpecWork
from workflows.software_factory.events.tickets import TicketEscalated
from loop import RunStatus
from loop.shared.github.issues.graph import SpecGraphSnapshot
from loop.shared.github.issues.spec import HITL_LABEL
from workflows.software_factory.stages.chore import chore_subject

__all__ = [
    "HELD_SPEC", "HELD_TICKET", "HITL", "SOURCE", "SpecWork", "chore_subject", "held_by_spec", "held_by_ticket",
    "release_reason", "spec_revision",
]

# The label that hands a Spec or Ticket to a person, and that a person removes to release it.
HITL = HITL_LABEL

# Prefixes every comment the Stages write on the tracker.
SOURCE = "implement_spec"

# Run attributes a hand-off records on the closed run, so intake can tell what a person holds.
HELD_SPEC = "implement_spec.held.spec"
HELD_TICKET = "implement_spec.held.ticket"

type RunAttributes = Mapping[str, str | int | bool]


def spec_revision(spec: SpecGraphSnapshot) -> str:
    """The revision of what the Spec is about: a digest of its body."""
    return hashlib.sha256(spec.body.encode()).hexdigest()


def held_by_spec(event: SpecHandedToHuman | SpecBlocked, reason: str | None) -> RunAttributes:
    """The run attributes of a hand-off that labelled the Spec; a block with no work to hold back labelled nothing."""
    labelled = event.labelled if isinstance(event, SpecBlocked) else True
    return {HELD_SPEC: True} if labelled else {}


def held_by_ticket(event: TicketEscalated, reason: str | None) -> RunAttributes:
    """The run attributes of an escalation, which labelled both the Ticket and its Spec."""
    return {HELD_SPEC: True, HELD_TICKET: event.ticket}


def release_reason(status: RunStatus | None, spec: SpecGraphSnapshot) -> str | None:
    """Why the work a person held is released, or None: the latest run ended with hand-offs recorded and the labels they set are gone."""
    if status is None or status.released or status.status not in ("handed_off", "failed"):
        return None
    held_spec = bool(status.attributes.get(HELD_SPEC))
    held_ticket = status.attributes.get(HELD_TICKET)
    if not held_spec and held_ticket is None:
        return None
    if spec.awaiting_human:
        return None
    if held_ticket is not None and any(ticket.number == held_ticket and HITL in ticket.labels for ticket in spec.tickets):
        return None
    return f"{HITL} was removed"
