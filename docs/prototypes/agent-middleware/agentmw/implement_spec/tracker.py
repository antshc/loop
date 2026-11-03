"""The issue tracker and pull requests the Workflow and its middlewares read and write."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .messages import ProposedTicket


@dataclass(frozen=True)
class Ticket:
    """A sub-issue of the Spec; `blocked_by` holds Ticket numbers."""

    number: int
    title: str
    body: str
    state: str = "open"
    labels: tuple[str, ...] = ()
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class Spec:
    """A Spec issue and its Tickets."""

    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...] = ()
    tickets: tuple[Ticket, ...] = ()

    def open_tickets(self) -> list[Ticket]:
        """The Tickets not closed yet."""
        return [ticket for ticket in self.tickets if ticket.state == "open"]

    def ready_tickets(self, held_label: str) -> list[Ticket]:
        """Open Tickets no person holds whose blockers are all closed, in order."""
        closed = {ticket.number for ticket in self.tickets if ticket.state != "open"}
        return [
            ticket
            for ticket in self.open_tickets()
            if held_label not in ticket.labels and set(ticket.blocked_by) <= closed
        ]


@dataclass(frozen=True)
class PullRequest:
    """The feature branch's pull request."""

    number: int
    url: str
    branch: str
    draft: bool = True


class Tracker(Protocol):
    """GitHub issues and pull requests of the target repository."""

    async def spec(self, number: int) -> Spec: ...

    async def ticket(self, number: int) -> Ticket: ...

    async def create_tickets(
        self, spec: int, tickets: Sequence[ProposedTicket]
    ) -> tuple[int, ...]: ...

    async def comment(self, number: int, body: str) -> None: ...

    async def add_label(self, number: int, label: str) -> None: ...

    async def close(self, number: int, comment: str) -> None: ...

    async def open_pull_request(self, branch: str) -> PullRequest | None: ...

    async def publish_draft(self, branch: str, title: str) -> PullRequest: ...

    async def diff(self, pull_request: PullRequest) -> str: ...

    async def mark_ready(self, pull_request: PullRequest) -> None: ...
