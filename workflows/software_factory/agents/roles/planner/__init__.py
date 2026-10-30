"""The planner role: proposes the Tickets that deliver a Spec, read-only."""

from __future__ import annotations

import json
from dataclasses import replace

from loop import DEFAULT, Role

from ... import _kit
from loop import Declined, ReplyMismatch, TypedAgent
from .messages import PlannerReply, PlannerRequest, Proposed, ProposedTicket


def _ticket(item: dict) -> ProposedTicket:
    blockers = item.get("blocked_by", [])
    if not isinstance(blockers, list) or not all(isinstance(blocker, int) and not isinstance(blocker, bool) for blocker in blockers):
        raise ReplyMismatch(f"planned ticket blocked_by must be a list of indexes: {item!r}")
    return ProposedTicket(_kit.text(item, "title", required=True).strip(), _kit.text(item, "body"), tuple(blockers))


def _decode(request: PlannerRequest, output: str) -> PlannerReply:
    envelope = _kit.read_status(output)
    if envelope.failed:
        return envelope.declined()
    return Proposed(tuple(_ticket(item) for item in _kit.objects(envelope.result, "tickets", "planned ticket")))


PLANNER: TypedAgent[PlannerRequest, PlannerReply] = _kit.template_agent(
    "planner",
    package=__name__,
    profile=replace(DEFAULT, name=Role.PLANNER),
    args={
        "CHECKOUT_PATH": lambda request: str(request.checkout),
        "SPEC_JSON": lambda request: json.dumps(request.spec, indent=2),
        "TARGET_BRANCH": lambda request: request.base_branch,
        "FEATURE_BRANCH": lambda request: request.feature_branch,
    },
    decode=_decode,
)

__all__ = ["PLANNER", "Declined", "PlannerReply", "PlannerRequest", "Proposed", "ProposedTicket"]
