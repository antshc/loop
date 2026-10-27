"""The harness repo's Spec/Ticket tracker on GitHub issues and sub-issues."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from workflows.shared.github.repo import GhRunner, GitHubRepo
from workflows.shared.process import CommandError

from ..model.records import Comment, PlannedTicket, Spec, Ticket, TicketWorkflowError, _AddLabel, _Change, _CommentOn

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


def _with_blockers(planned: PlannedTicket, earlier: list[int]) -> str:
    blockers = [f"#{earlier[index]}" for index in planned.blocked_by if 0 <= index < len(earlier)]
    return f"{planned.body.rstrip()}\n\nBlocked by {', '.join(blockers)}" if blockers else planned.body


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
    """The harness repo's Spec/Ticket tracker: loads Specs through `gh` and persists the changes made to them."""

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

    def update_spec(self, spec: Spec) -> None:
        """Persists the Spec's and its Tickets' pending changes in order; a failed change stays pending."""
        for change in spec.pending_changes:
            self._apply(change)
            spec.acknowledge(change)

    def create_tickets(self, spec: Spec, planned: Iterable[PlannedTicket]) -> tuple[Ticket, ...]:
        """Creates each planned Ticket as an open sub-issue of `spec`, in order, and adds them to `spec.tickets`.

        `blocked_by` indexes become `Blocked by #N` lines in the body. A failure raises
        `TicketWorkflowError` naming the sub-issues already created, which stay on the tracker.
        """
        slug = f"{self._owner}/{self._name}"
        created: list[Ticket] = []
        try:
            for planned_ticket in planned:
                body = _with_blockers(planned_ticket, [ticket.number for ticket in created])
                output = self._gh(("issue", "create", "--repo", slug, "--title", planned_ticket.title, "--body", body)).strip()
                match = re.search(r"/issues/(\d+)", output)
                if match is None:
                    raise TicketWorkflowError(f"cannot read the number of the created issue from {output!r}")
                number = int(match.group(1))
                issue_id = self._gh(("api", f"repos/{slug}/issues/{number}", "--jq", ".id")).strip()
                self._gh(("api", "--method", "POST", f"repos/{slug}/issues/{spec.number}/sub_issues", "-F", f"sub_issue_id={issue_id}"))
                created.append(Ticket(number, planned_ticket.title, "open", (), output, body))
        except (CommandError, TicketWorkflowError) as exception:
            made = ", ".join(f"#{ticket.number}" for ticket in created) or "none"
            raise TicketWorkflowError(f"creating Tickets failed after creating {made}: {exception}") from exception
        spec.tickets = (*spec.tickets, *created)
        spec.sub_issue_total += len(created)
        return tuple(created)

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

    def _apply(self, change: _Change) -> None:
        slug = f"{self._owner}/{self._name}"
        number = str(change.number)
        if isinstance(change, _CommentOn):
            self._gh(("issue", "comment", number, "--repo", slug, "--body", change.body))
        elif isinstance(change, _AddLabel):
            self._gh(("issue", "edit", number, "--repo", slug, "--add-label", change.label))
        else:
            self._gh(("issue", "close", number, "--repo", slug, "--comment", change.comment))
