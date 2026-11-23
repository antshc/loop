"""The typed `result` of each role's `completed` envelope; `failed` raises `AgentDeclined`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposedTicket:
    """One Ticket the planner proposes; `blocked_by` holds indexes of earlier proposed Tickets."""

    title: str
    body: str = ""
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class Proposed:
    """The planner's Tickets in dependency order."""

    tickets: tuple[ProposedTicket, ...]


@dataclass(frozen=True)
class Delivered:
    """The developer committed the Ticket."""

    commit: str
    summary: str
    verification: str


@dataclass(frozen=True)
class Refined:
    """The chore's proposed commit message and the findings it left alone; all may be empty."""

    commit_message: str = ""
    summary: str = ""
    out_of_scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewedAxis:
    """The outcome of one review axis, such as `bugs` or `quality`."""

    name: str
    outcome: str
    note: str = ""


@dataclass(frozen=True)
class ReviewedFinding:
    """One finding of a review axis."""

    kind: str
    summary: str
    axis: str = ""


@dataclass(frozen=True)
class Reviewed:
    """Per-axis outcomes, findings, the comments the reviewer posted, and the head it reviewed."""

    axes: tuple[ReviewedAxis, ...] = ()
    findings: tuple[ReviewedFinding, ...] = ()
    posted: tuple[str, ...] = ()
    head: str = ""
