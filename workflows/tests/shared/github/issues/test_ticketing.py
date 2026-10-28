from __future__ import annotations

import pytest

from workflows.shared.github.issues.client import Issues
from workflows.shared.github.issues.readiness import TicketReadiness
from workflows.shared.github.issues.ticketing import SpecTicketingService, TicketDraft, TicketingError
from workflows.shared.github.repo import GitHubRepo
from workflows.tests.shared.fakes import FakeGhCli, issue

_ROUTING = ("spec", "repo:target:o/r", "repo:base:main")

_DRAFTS = (
    TicketDraft("0-basket", "Add basket", "Create a basket"),
    TicketDraft("1-coffee", "Add coffee", "Add a product", blocked_by=(0,)),
    TicketDraft("2-checkout", "Add checkout", "Pay", blocked_by=(0, 1)),
)


def _setup() -> tuple[FakeGhCli, Issues, SpecTicketingService]:
    gh = FakeGhCli(specs=[issue(1, "Add login page", labels=_ROUTING)], tickets={1: []})
    issues = Issues(GitHubRepo("owner", "repo", gh=gh))
    return gh, issues, SpecTicketingService(issues)


def test_each_ordering_becomes_a_native_edge_and_no_body_carries_a_blocked_by_line() -> None:
    gh, issues, ticketing = _setup()

    created = ticketing.create(next(iter(issues.specs())), _DRAFTS)

    first, second, third = (ticket.number for ticket in created.tickets)
    assert [gh.blockers_of(number) for number in (first, second, third)] == [[], [first], [first, second]]
    assert [(edge.ticket.number, edge.blocker.number) for edge in created.blocked_by] == [(second, first), (third, first), (third, second)]
    assert not any("Blocked by" in body for _, body in gh.created_issues())
    graph = next(iter(issues.specs()))
    assert [ticket.number for ticket in graph.tickets] == [first, second, third]
    assert graph.readiness == {first: TicketReadiness.READY, second: TicketReadiness.BLOCKED, third: TicketReadiness.BLOCKED}


def test_edges_are_added_before_their_ticket_is_linked_to_the_spec() -> None:
    gh, issues, ticketing = _setup()

    created = ticketing.create(next(iter(issues.specs())), _DRAFTS)

    second = created.tickets[1].number
    writes = [call[3] for call in gh.writes() if call[3].endswith(("/dependencies/blocked_by", "/sub_issues"))]
    assert writes.index(f"repos/owner/repo/issues/{second}/dependencies/blocked_by") < writes.index(
        "repos/owner/repo/issues/1/sub_issues", writes.index(f"repos/owner/repo/issues/{second}/dependencies/blocked_by"),
    )


def test_a_retry_after_creating_everything_creates_no_ticket_and_no_second_edge_or_link() -> None:
    gh, issues, ticketing = _setup()
    spec = next(iter(issues.specs()))
    first_run = ticketing.create(spec, _DRAFTS)
    gh.calls.clear()

    second_run = ticketing.create(spec, _DRAFTS)

    assert [ticket.number for ticket in second_run.tickets] == [ticket.number for ticket in first_run.tickets]
    assert gh.writes() == []
    assert [len(gh.blockers_of(ticket.number)) for ticket in first_run.tickets] == [0, 1, 2]


def test_a_retry_after_a_crash_adds_only_the_missing_edges_and_links() -> None:
    gh, issues, ticketing = _setup()
    spec = next(iter(issues.specs()))
    gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues", after=1)

    with pytest.raises(TicketingError) as failure:
        ticketing.create(spec, _DRAFTS)

    first = failure.value.created[0]
    assert [ticket.number for ticket in failure.value.created] == [first.number]
    assert f"after creating #{first.number}" in str(failure.value)
    gh.calls.clear()

    created = ticketing.create(spec, _DRAFTS)

    assert [title for title, _ in gh.created_issues()] == ["Add checkout"]
    assert not gh.calls_matching("api", "--method", "POST", f"repos/owner/repo/issues/{created.tickets[1].number}/dependencies/blocked_by")
    assert [len(gh.blockers_of(ticket.number)) for ticket in created.tickets] == [0, 1, 2]
    assert [ticket.number for ticket in next(iter(issues.specs())).tickets] == [ticket.number for ticket in created.tickets]


def test_a_failure_to_add_an_edge_names_the_tickets_already_made_and_links_nothing_more() -> None:
    gh, issues, ticketing = _setup()
    gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/102/dependencies/blocked_by")

    with pytest.raises(TicketingError, match="after creating #101"):
        ticketing.create(next(iter(issues.specs())), _DRAFTS)

    assert [call[3] for call in gh.calls_matching("api", "--method", "POST") if call[3].endswith("/sub_issues")] == [
        "repos/owner/repo/issues/1/sub_issues",
    ]
