"""Harness tracker writes: failure counts, human hand-offs, and delivery notices."""

from __future__ import annotations

from collections.abc import Sequence

from loop import ExecutionStore, GitHubClient, Ticket

from .result import DevResult
from .settings import HITL_LABEL


def record_ticket_failure(store: ExecutionStore, harness_slug: str, ticket: Ticket) -> int:
    """Counts one failure for `ticket` and returns its persisted total."""
    owner, repo = harness_slug.split("/", 1)
    store.record_failure(
        ticket.url,
        owner=owner,
        repo=repo,
        task_id=str(ticket.number),
        title=ticket.title,
        items=[ticket.number],
    )
    return store.failed_attempts(ticket.url)


def hitl(github: GitHubClient, number: int, message: str) -> None:
    """Hands issue `number` to a human with `message`."""
    github.add_label(number, HITL_LABEL)
    github.comment(number, message)


def escalate(github: GitHubClient, spec_number: int, ticket_number: int, reason: str) -> None:
    """Hands both the Ticket and its Spec to a human."""
    message = f"dev: {reason}"
    hitl(github, ticket_number, message)
    hitl(github, spec_number, message)


def block_spec(github: GitHubClient, spec_number: int, actionable: Sequence[Ticket], message: str) -> None:
    """Hands the Spec to a human, but only when there is work it is blocking."""
    if actionable:
        hitl(github, spec_number, message)


def close_delivered(github: GitHubClient, ticket_number: int, dev_result: DevResult) -> None:
    """Closes a delivered Ticket with its commit, summary, and verification."""
    github.close_with_comment(
        ticket_number,
        f"Delivered in {dev_result.commit}.\n\n{dev_result.summary}\n\n{dev_result.verification}",
    )


def announce_delivered(github: GitHubClient, spec_number: int, pull_request_url: str) -> None:
    """Tells the Spec that all its Tickets are delivered and where the draft PR is."""
    github.comment(spec_number, f"dev: all Tickets delivered; draft pull request: {pull_request_url}")
