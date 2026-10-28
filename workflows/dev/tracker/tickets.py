"""The harness repo's Spec/Ticket tracker on GitHub issues and sub-issues."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from workflows.shared.github.repo import GhRunner, GitHubRepo

from ..model.records import Comment, Spec, Ticket, TicketWorkflowError

_ISSUE_FIELDS = (
    "number title url state body labels(first: 20) { nodes { name } }"
    " comments(first: 50) { nodes { author { login } body createdAt } }"
)


_SPECS_QUERY = (
    "query($owner: String!, $repo: String!) {"
    " repository(owner: $owner, name: $repo) {"
    f'  issues(first: 100, states: OPEN, labels: ["spec"]) {{ nodes {{ {_ISSUE_FIELDS} }} }}'
    " } }"
)


_SUB_ISSUES_QUERY = (
    "query($owner: String!, $repo: String!, $number: Int!) {"
    " repository(owner: $owner, name: $repo) {"
    f"  issue(number: $number) {{ subIssues(first: 100) {{ nodes {{ {_ISSUE_FIELDS} }} }} }}"
    " } }"
)


def _comments(node: dict[str, Any]) -> tuple[Comment, ...]:
    return tuple(
        # `author` is null for deleted accounts.
        Comment((comment["author"] or {}).get("login", "ghost"), comment["body"], comment["createdAt"])
        for comment in node["comments"]["nodes"]
    )


def _labels(node: dict[str, Any]) -> tuple[str, ...]:
    return tuple(label["name"] for label in node["labels"]["nodes"])


@dataclass(frozen=True)
class _IssueState:
    """What GitHub holds on one issue right now."""

    is_open: bool
    labels: frozenset[str]
    comments: tuple[str, ...]


def _fingerprint(purpose: str, body: str) -> str:
    """A hidden marker stable for the Comment's purpose and content, so a retry can recognise it."""
    digest = hashlib.sha256(f"{purpose}\n{body}".encode()).hexdigest()[:16]
    return f"<!-- loop:comment {purpose}:{digest} -->"


def _ticket_marker(spec: Spec, key: str) -> str:
    """A hidden marker tying a Ticket body to its Spec and planned-Ticket key."""
    return f"<!-- loop:ticket spec={spec.number} key={key} -->"


def _routing_label(spec: Spec) -> str:
    label = spec.target_label
    if label is None:
        raise TicketWorkflowError(f"cannot resolve the target repository of Spec {spec.url}")
    return label


def _spec(node: dict[str, Any], tickets: tuple[Ticket, ...], total: int = 0, held: tuple[int, ...] = ()) -> Spec:
    return Spec(
        number=node["number"],
        title=node["title"],
        url=node["url"],
        labels=_labels(node),
        body=node["body"],
        comments=_comments(node),
        tickets=tickets,
        sub_issue_total=total,
        held_tickets=held,
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
    """The harness repo's Spec/Ticket tracker: loads Specs through `gh` and applies each change at once."""

    def __init__(self, owner: str, name: str, *, gh: GhRunner) -> None:
        self._owner = owner
        self._name = name
        self._gh = gh

    @classmethod
    def of(cls, github: GitHubRepo) -> TicketsTracker:
        """The tracker on `github`'s issues."""
        return cls(github.owner, github.name, gh=github.run)

    def specs(self) -> Iterable[Spec]:
        """Every open Spec on the harness tracker, each with its deliverable Tickets."""
        specs = []
        for node in self._spec_issues():
            tickets = tuple(_ticket(item) for item in self._sub_issues(node["number"]))
            actionable = tuple(ticket for ticket in tickets if ticket.actionable)
            held = tuple(ticket.number for ticket in tickets if ticket.state == "open" and not ticket.actionable)
            specs.append(_spec(node, actionable, len(tickets), held))
        return specs

    def comment(self, number: int, purpose: str, body: str) -> None:
        """Posts `body` on the issue, unless a Comment with the same purpose and content is already there."""
        marker = _fingerprint(purpose, body)
        if any(marker in existing for existing in self._state(number).comments):
            return
        self._gh(("issue", "comment", str(number), "--repo", self._slug, "--body", f"{body}\n\n{marker}"))

    def add_label(self, number: int, label: str) -> None:
        """Adds `label` to the issue, unless it already carries it."""
        if label not in self._state(number).labels:
            self._gh(("issue", "edit", str(number), "--repo", self._slug, "--add-label", label))

    def remove_label(self, number: int, label: str) -> None:
        """Removes `label` from the issue, unless it does not carry it."""
        if label in self._state(number).labels:
            self._gh(("issue", "edit", str(number), "--repo", self._slug, "--remove-label", label))

    def close(self, number: int, comment: str) -> None:
        """Closes the issue with `comment`, unless it is already closed."""
        if self._state(number).is_open:
            self._gh(("issue", "close", str(number), "--repo", self._slug, "--comment", comment))

    def find_ticket(self, spec: Spec, key: str) -> Ticket | None:
        """The open issue already created for `key` of `spec`, whether or not it is linked, else None."""
        label, marker = _routing_label(spec), _ticket_marker(spec, key)
        output = self._gh((
            "issue", "list", "--repo", self._slug, "--state", "open", "--label", label, "--limit", "200",
            "--json", "number,title,url,body",
        ))
        for node in json.loads(output or "[]"):
            if marker in node["body"] and node["number"] != spec.number:
                return Ticket(node["number"], node["title"], "open", (label,), node["url"], node["body"])
        return None

    def create_ticket(self, spec: Spec, key: str, title: str, body: str) -> Ticket:
        """Creates an open issue for `key` of `spec`, marked so a retry finds it; it is not yet linked to `spec`."""
        label = _routing_label(spec)
        marked = f"{body.rstrip()}\n\n{_ticket_marker(spec, key)}"
        output = self._gh((
            "issue", "create", "--repo", self._slug, "--title", title, "--label", label, "--body", marked,
        )).strip()
        match = re.search(r"/issues/(\d+)", output)
        if match is None:
            raise TicketWorkflowError(f"cannot read the number of the created issue from {output!r}")
        return Ticket(int(match.group(1)), title, "open", (label,), output, marked)

    def link_ticket(self, spec: Spec, ticket: Ticket) -> None:
        """Makes `ticket` a sub-issue of `spec`, unless it already is one."""
        if any(node["number"] == ticket.number for node in self._sub_issues(spec.number)):
            return
        issue_id = self._gh(("api", f"repos/{self._slug}/issues/{ticket.number}", "--jq", ".id")).strip()
        self._gh((
            "api", "--method", "POST", f"repos/{self._slug}/issues/{spec.number}/sub_issues", "-F", f"sub_issue_id={issue_id}",
        ))

    @property
    def _slug(self) -> str:
        return f"{self._owner}/{self._name}"

    def _state(self, number: int) -> _IssueState:
        output = self._gh(("issue", "view", str(number), "--repo", self._slug, "--json", "state,labels,comments"))
        node = json.loads(output)
        return _IssueState(
            node["state"].upper() == "OPEN",
            frozenset(label["name"] for label in node["labels"]),
            tuple(comment["body"] for comment in node["comments"]),
        )

    def _tickets(self, spec_number: int) -> tuple[Ticket, ...]:
        tickets = (_ticket(node) for node in self._sub_issues(spec_number))
        return tuple(ticket for ticket in tickets if ticket.actionable)

    def _spec_issues(self) -> list[dict[str, Any]]:
        pages = json.loads(self._graphql(_SPECS_QUERY))
        return [node for page in pages for node in page["data"]["repository"]["issues"]["nodes"]]

    def _sub_issues(self, number: int) -> list[dict[str, Any]]:
        pages = json.loads(self._graphql(_SUB_ISSUES_QUERY, number=number))
        return [node for page in pages for node in page["data"]["repository"]["issue"]["subIssues"]["nodes"]]

    def _graphql(self, query: str, **variables: int) -> str:
        args = ["api", "graphql", "--paginate", "--slurp", "-f", f"query={query}"]
        args += ["-f", f"owner={self._owner}", "-f", f"repo={self._name}"]
        for name, value in variables.items():
            args += ["-F", f"{name}={value}"]
        return self._gh(tuple(args))
