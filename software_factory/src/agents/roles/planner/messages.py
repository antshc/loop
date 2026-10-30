"""The planner's request and replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...contract import Declined, Json, Message


@dataclass(frozen=True)
class PlannerRequest(Message):
    """One Spec to break into Tickets, planned read-only on the checkout at `checkout`."""

    checkout: Path
    spec: Json
    base_branch: str
    feature_branch: str


@dataclass(frozen=True)
class ProposedTicket(Message):
    """One Ticket the planner proposes; `blocked_by` holds zero-based indexes of earlier proposed Tickets."""

    title: str
    body: str
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class Proposed(Message):
    """The Tickets in dependency order."""

    tickets: tuple[ProposedTicket, ...]


type PlannerReply = Proposed | Declined
