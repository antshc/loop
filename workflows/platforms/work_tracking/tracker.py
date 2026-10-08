"""Spec/Ticket entities and the harness tracker: Spec metadata, actionable Tickets, hand-offs, persisted Spec changes."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .gh_client import GitHubClient

HITL_LABEL = "hitl"
_BLOCKING_LABELS = frozenset({HITL_LABEL, "spec"})
_INITIATIVE = re.compile(r"^(?P<initiative>[^:]+):\s*(?P<title>.+)$")
_TARGET_PREFIX = "repo:target:"
_BASE_PREFIX = "repo:base:"
_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class _CommentOn:
    number: int
    body: str


@dataclass(frozen=True)
class _AddLabel:
    number: int
    label: str


@dataclass(frozen=True)
class _Close:
    number: int
    comment: str


_Change = _CommentOn | _AddLabel | _Close


@dataclass
class Spec:
    """Aggregate root: its Tickets change only through it, and `TicketsTracker.update_spec` persists the changes."""

    number: int
    title: str
    url: str
    labels: tuple[str, ...]
    body: str = ""
    comments: tuple[Comment, ...] = ()
    # Open Tickets ready for delivery, in order; they live on the harness tracker even when the Spec targets another repo.
    tickets: tuple[Ticket, ...] = ()
    # Changes made since the last update_spec, in the order GitHub must see them.
    _changes: list[_Change] = field(default_factory=list, init=False, repr=False, compare=False)

    @property
    def awaiting_human(self) -> bool:
        """Carries the `hitl` label."""
        return HITL_LABEL in self.labels

    @property
    def has_work(self) -> bool:
        """Has at least one Ticket ready for delivery."""
        return any(ticket.actionable for ticket in self.tickets)

    @property
    def pending_changes(self) -> tuple[_Change, ...]:
        """Changes not yet persisted, oldest first."""
        return tuple(self._changes)

    def acknowledge(self, change: _Change) -> None:
        """Marks the oldest occurrence of `change` as persisted."""
        self._changes.remove(change)

    def comment(self, message: str) -> None:
        """Comments `message` on the Spec."""
        self._changes.append(_CommentOn(self.number, message))

    def hand_to_human(self, message: str) -> None:
        """Labels the Spec `hitl` and comments `message` on it."""
        self.labels = _with_label(self.labels, HITL_LABEL)
        self._changes += [_AddLabel(self.number, HITL_LABEL), _CommentOn(self.number, message)]

    def block(self, message: str) -> None:
        """Hands the Spec to a human, but only when there is work it is blocking."""
        if self.has_work:
            self.hand_to_human(message)

    def escalate(self, ticket_number: int, reason: str) -> None:
        """Hands both the Ticket and the Spec to a human."""
        message = f"dev: {reason}"
        ticket = self._ticket(ticket_number)
        ticket.labels = _with_label(ticket.labels, HITL_LABEL)
        self._changes += [_AddLabel(ticket_number, HITL_LABEL), _CommentOn(ticket_number, message)]
        self.hand_to_human(message)

    def close_ticket(self, ticket_number: int, comment: str) -> None:
        """Closes a delivered Ticket with `comment`."""
        self._ticket(ticket_number).state = "closed"
        self._changes.append(_Close(ticket_number, comment))

    def announce_delivered(self, pull_request_url: str) -> None:
        """Tells the Spec that all its Tickets are delivered and where the draft PR is."""
        self.comment(f"dev: all Tickets delivered; draft pull request: {pull_request_url}")

    def _ticket(self, number: int) -> Ticket:
        for ticket in self.tickets:
            if ticket.number == number:
                return ticket
        raise ValueError(f"ticket #{number} does not belong to spec #{self.number}")

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

    @property
    def feature_branch(self) -> str | None:
        """`<version_with_underscores>_<title slug>` when base_branch carries a version, else `<title slug>`; None without a base_branch."""
        base_branch = self.base_branch
        if base_branch is None:
            return None
        slug = slugify(self.bare_title)
        match = _VERSION.search(base_branch)
        return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"


@dataclass
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


def _with_label(labels: tuple[str, ...], label: str) -> tuple[str, ...]:
    return labels if label in labels else (*labels, label)


def _comments(node: dict[str, Any]) -> tuple[Comment, ...]:
    return tuple(
        # `author` is null for deleted accounts.
        Comment((comment["author"] or {}).get("login", "ghost"), comment["body"], comment["createdAt"])
        for comment in node["comments"]["nodes"]
    )


def _labels(node: dict[str, Any]) -> tuple[str, ...]:
    return tuple(label["name"] for label in node["labels"]["nodes"])


def _spec(node: dict[str, Any], tickets: tuple[Ticket, ...]) -> Spec:
    return Spec(
        number=node["number"],
        title=node["title"],
        url=node["url"],
        labels=_labels(node),
        body=node["body"],
        comments=_comments(node),
        tickets=tickets,
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
    """The harness repo's Spec/Ticket tracker: loads Specs and persists the changes made to them."""

    def __init__(self, github: GitHubClient) -> None:
        self.github = github

    def specs(self) -> Iterable[Spec]:
        """Every open Spec on the harness tracker, each with its deliverable Tickets."""
        return [_spec(node, self._tickets(node["number"])) for node in self.github.spec_issues()]

    def _tickets(self, spec_number: int) -> tuple[Ticket, ...]:
        tickets = (_ticket(node) for node in self.github.sub_issues(spec_number))
        return tuple(ticket for ticket in tickets if ticket.actionable)

    def update_spec(self, spec: Spec) -> None:
        """Persists the Spec's and its Tickets' pending changes in order; a failed change stays pending."""
        for change in spec.pending_changes:
            self._apply(change)
            spec.acknowledge(change)

    def _apply(self, change: _Change) -> None:
        if isinstance(change, _CommentOn):
            self.github.comment(change.number, change.body)
        elif isinstance(change, _AddLabel):
            self.github.add_label(change.number, change.label)
        else:
            self.github.close_with_comment(change.number, change.comment)
