"""In-memory issue models for tests that need no `gh` fake."""

from __future__ import annotations

from workflows.shared.github.identity import IssueIdentity, IssueRef, RepositoryId
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.issue import Issue, IssueState
from workflows.shared.github.issues.spec import Spec, Ticket


REPOSITORY = RepositoryId("owner", "repo")


def build_issue(
    number: int = 1,
    title: str = "Add login page",
    *,
    labels: tuple[str, ...] = (),
    state: IssueState = IssueState.OPEN,
    issue_type: str | None = None,
    body: str = "",
) -> Issue:
    """An issue with stable identity derived from its number."""
    return Issue(
        ref=IssueRef(REPOSITORY, number),
        identity=IssueIdentity(1000 + number, f"I_kwDO{number}"),
        title=title,
        body=body,
        url=f"https://github.com/{REPOSITORY}/issues/{number}",
        state=state,
        labels=labels,
        issue_type=issue_type,
    )


def build_spec(number: int = 1, title: str = "Add login page", labels: tuple[str, ...] = ("spec",)) -> Spec:
    """A Spec carrying `labels`."""
    return Spec.of(build_issue(number, title, labels=labels))


def build_ticket(number: int = 10, title: str = "Add login form", labels: tuple[str, ...] = (), state: IssueState = IssueState.OPEN) -> Ticket:
    """A Ticket in `state` carrying `labels`."""
    return Ticket.of(build_issue(number, title, labels=labels, state=state))


def build_graph(
    number: int = 1,
    title: str = "Add login page",
    labels: tuple[str, ...] = ("spec",),
    tickets: tuple[Ticket, ...] = (),
) -> SpecGraphSnapshot:
    """A complete Spec graph whose `tickets` were all read open or held."""
    return SpecGraphSnapshot(
        spec=build_spec(number, title, labels),
        tickets=tickets,
        sub_issue_total=len(tickets),
        held_tickets=tuple(ticket.number for ticket in tickets if not ticket.actionable),
    )
