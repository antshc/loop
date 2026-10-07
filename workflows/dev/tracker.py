"""Harness tracker writes: failure counts, human hand-offs, and delivery notices."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from loop import ExecutionStore, GitHubClient, Spec, Ticket

from .result import DevResult
from .settings import HITL_LABEL


class TicketsTracker:
    """The harness repo's Spec/Ticket tracker and each Ticket's persisted failure count."""

    def __init__(self, github: GitHubClient, store: ExecutionStore, harness_slug: str) -> None:
        self.github = github
        self._store = store
        self._harness_slug = harness_slug

    def specs(self) -> Iterable[Spec]:
        """Every open Spec on the harness tracker."""
        return self.github.get_specs()

    def get_tickets(self, spec: Spec) -> tuple[Ticket, ...]:
        """The Spec's open Tickets ready for delivery, in order."""
        return tuple(self.github.get_actionable_issues(spec))

    def comment(self, number: int, message: str) -> None:
        """Comments `message` on issue `number`."""
        self.github.comment(number, message)

    def record_failure(self, ticket: Ticket) -> int:
        """Counts one failure for `ticket` and returns its persisted total."""
        owner, repo = self._harness_slug.split("/", 1)
        self._store.record_failure(
            ticket.url,
            owner=owner,
            repo=repo,
            task_id=str(ticket.number),
            title=ticket.title,
            items=[ticket.number],
        )
        return self._store.failed_attempts(ticket.url)

    def hitl(self, number: int, message: str) -> None:
        """Hands issue `number` to a human with `message`."""
        self.github.add_label(number, HITL_LABEL)
        self.github.comment(number, message)

    def escalate(self, spec_number: int, ticket_number: int, reason: str) -> None:
        """Hands both the Ticket and its Spec to a human."""
        message = f"dev: {reason}"
        self.hitl(ticket_number, message)
        self.hitl(spec_number, message)

    def block_spec(self, spec_number: int, tickets: Sequence[Ticket], message: str) -> None:
        """Hands the Spec to a human, but only when there is work it is blocking."""
        if tickets:
            self.hitl(spec_number, message)

    def close_delivered(self, ticket: Ticket, dev_result: DevResult) -> None:
        """Closes a delivered Ticket with its commit, summary, and verification, and clears its failures."""
        self.github.close_with_comment(
            ticket.number,
            f"Delivered in {dev_result.commit}.\n\n{dev_result.summary}\n\n{dev_result.verification}",
        )
        self._store.reset(ticket.url)

    def announce_delivered(self, spec_number: int, pull_request_url: str) -> None:
        """Tells the Spec that all its Tickets are delivered and where the draft PR is."""
        self.github.comment(spec_number, f"dev: all Tickets delivered; draft pull request: {pull_request_url}")
