"""Spec/Ticket entities and the harness tracker: Spec metadata, actionable Tickets, failure counts, hand-offs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from loop import ExecutionStore

from .gh_client import GitHubClient

HITL_LABEL = "hitl"
_BLOCKING_LABELS = frozenset({HITL_LABEL, "spec"})
_INITIATIVE = re.compile(r"^(?P<initiative>[^:]+):\s*(?P<title>.+)$")
_TARGET_PREFIX = "repo:target:"
_BASE_PREFIX = "repo:base:"
_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class Spec:
    number: int
    title: str
    url: str
    labels: tuple[str, ...]
    body: str = ""
    comments: tuple[Comment, ...] = ()

    @property
    def initiative(self) -> str:
        """The `<initiative>: <title>` title prefix, or the Spec number when absent."""
        match = _INITIATIVE.match(self.title)
        return str(self.number) if match is None else match["initiative"].strip()

    @property
    def bare_title(self) -> str:
        """The title without its Initiative prefix."""
        match = _INITIATIVE.match(self.title)
        return self.title if match is None else match["title"].strip()

    @property
    def target(self) -> str | None:
        """The single `repo:target:<owner/name>` label's value, or None when missing/malformed/duplicated."""
        values = [label[len(_TARGET_PREFIX) :] for label in self.labels if label.startswith(_TARGET_PREFIX)]
        if len(values) != 1 or not _SLUG.match(values[0]):
            return None
        return values[0]

    @property
    def base_branch(self) -> str | None:
        """The single `repo:base:<branch>` label's value, or None when missing/empty/duplicated."""
        values = [label[len(_BASE_PREFIX) :] for label in self.labels if label.startswith(_BASE_PREFIX)]
        return values[0] if len(values) == 1 and values[0] else None


@dataclass(frozen=True)
class Ticket:
    number: int
    title: str
    state: str
    labels: tuple[str, ...]
    url: str
    body: str = ""
    comments: tuple[Comment, ...] = ()

    @property
    def actionable(self) -> bool:
        """Open and carrying neither the `hitl` nor the `spec` label."""
        return self.state == "open" and not ({label.casefold() for label in self.labels} & _BLOCKING_LABELS)


def _comments(node: dict[str, Any]) -> tuple[Comment, ...]:
    return tuple(
        # `author` is null for deleted accounts.
        Comment((comment["author"] or {}).get("login", "ghost"), comment["body"], comment["createdAt"])
        for comment in node["comments"]["nodes"]
    )


def _labels(node: dict[str, Any]) -> tuple[str, ...]:
    return tuple(label["name"] for label in node["labels"]["nodes"])


def _spec(node: dict[str, Any]) -> Spec:
    return Spec(
        number=node["number"],
        title=node["title"],
        url=node["url"],
        labels=_labels(node),
        body=node["body"],
        comments=_comments(node),
    )


def _ticket(node: dict[str, Any]) -> Ticket:
    return Ticket(
        number=node["number"],
        title=node["title"],
        state=node["state"].lower(),
        labels=_labels(node),
        url=node["url"],
        body=node["body"],
        comments=_comments(node),
    )


class TicketsTracker:
    """The harness repo's Spec/Ticket tracker and each Ticket's persisted failure count."""

    def __init__(self, github: GitHubClient, store: ExecutionStore, harness_slug: str) -> None:
        self.github = github
        self._store = store
        self._harness_slug = harness_slug

    def specs(self) -> Iterable[Spec]:
        """Every open Spec on the harness tracker."""
        return [_spec(node) for node in self.github.spec_issues()]

    def get_tickets(self, spec: Spec) -> tuple[Ticket, ...]:
        """The Spec's open Tickets ready for delivery, in order."""
        tickets = (_ticket(node) for node in self.github.sub_issues(spec.number))
        return tuple(ticket for ticket in tickets if ticket.actionable)

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

    def close_delivered(self, ticket: Ticket, comment: str) -> None:
        """Closes a delivered Ticket with `comment` and clears its failures."""
        self.github.close_with_comment(ticket.number, comment)
        self._store.reset(ticket.url)

    def announce_delivered(self, spec_number: int, pull_request_url: str) -> None:
        """Tells the Spec that all its Tickets are delivered and where the draft PR is."""
        self.github.comment(spec_number, f"dev: all Tickets delivered; draft pull request: {pull_request_url}")
