from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from loop.shared.github.issues.graph import IssueRelation, SpecGraphSnapshot
from loop.shared.github.issues.issue import CloseReason, GapState, IssueState, ReadGap
from loop.shared.github.issues.readiness import TicketReadiness

from tests.shared.builders import build_blocker, build_graph, build_ticket


def test_a_graph_without_gaps_is_complete() -> None:
    assert build_graph().complete


@pytest.mark.parametrize("state", [GapState.INCOMPLETE, GapState.UNKNOWN])
def test_a_graph_with_a_gap_is_incomplete(state: GapState) -> None:
    graph = replace(build_graph(), gaps=(ReadGap("sub_issues", state),))

    assert not graph.complete


def test_a_graph_is_incomplete_when_a_ticket_was_not_fully_read() -> None:
    ticket = replace(build_ticket(), gaps=(ReadGap("comments", GapState.INCOMPLETE),))

    assert not build_graph(tickets=(ticket,)).complete


def test_an_incomplete_graph_never_says_it_needs_a_plan() -> None:
    empty = build_graph()
    unread = replace(empty, gaps=(ReadGap("sub_issues", GapState.UNKNOWN),))

    assert empty.needs_plan and not unread.needs_plan


def test_a_graph_whose_sub_issues_are_all_closed_needs_no_plan() -> None:
    assert not replace(build_graph(), sub_issue_total=2).needs_plan


def test_work_is_a_deliverable_ticket_that_is_actionable() -> None:
    assert build_graph(tickets=(build_ticket(10),)).has_work
    assert not build_graph(tickets=(build_ticket(10, labels=("hitl",)),)).has_work
    assert not build_graph().has_work


def test_tickets_held_when_read_are_not_deliverable() -> None:
    graph = build_graph(tickets=(build_ticket(10), build_ticket(11, labels=("hitl",))))

    assert graph.held_tickets == (11,)
    assert [ticket.number for ticket in graph.deliverable_tickets] == [10]


def test_blocked_and_unknown_tickets_are_held_and_not_deliverable() -> None:
    graph = build_graph(
        tickets=(
            build_ticket(10),
            build_ticket(11),
            build_ticket(12),
            build_ticket(13),
        ),
        blocked_by=(
            build_blocker(11, 10),
            build_blocker(12, 20, IssueState.CLOSED, CloseReason.NOT_PLANNED),
        ),
        unread_dependencies=(13,),
    )

    assert graph.readiness == {
        10: TicketReadiness.READY,
        11: TicketReadiness.BLOCKED,
        12: TicketReadiness.BLOCKED,
        13: TicketReadiness.UNKNOWN,
    }
    assert graph.held_tickets == (11, 12, 13)
    assert [ticket.number for ticket in graph.deliverable_tickets] == [10]


def test_a_graph_whose_every_ticket_is_held_has_no_work_and_still_needs_no_plan() -> (
    None
):
    graph = build_graph(
        tickets=(build_ticket(10),), blocked_by=(build_blocker(10, 20),)
    )

    assert not graph.has_work and not graph.needs_plan


def test_closing_a_blocker_ticket_as_completed_readies_the_tickets_waiting_for_it() -> (
    None
):
    graph = build_graph(
        tickets=(build_ticket(10), build_ticket(11)),
        blocked_by=(build_blocker(11, 10),),
    )

    closed = graph.ticket_closed(10)

    assert graph.held_tickets == (11,)
    assert closed.held_tickets == () and closed.readiness[11] is TicketReadiness.READY
    assert [
        ticket.number for ticket in closed.deliverable_tickets if ticket.actionable
    ] == [11]


def test_created_tickets_carry_their_blocked_by_edges() -> None:
    graph = build_graph()
    first, second = build_ticket(10), build_ticket(11)

    grown = graph.with_created([first, second], [build_blocker(11, 10)])

    assert grown.held_tickets == (11,)
    assert graph.blocked_by == ()


def test_changing_a_graph_returns_a_new_snapshot_and_leaves_the_original() -> None:
    graph = build_graph(tickets=(build_ticket(10),))

    closed = graph.ticket_closed(10)
    escalated = graph.ticket_labeled(10, "hitl")
    handed = graph.labeled("hitl")

    assert graph.tickets[0].state == "open" and closed.tickets[0].state == "closed"
    assert (
        "hitl" in escalated.tickets[0].labels and "hitl" not in graph.tickets[0].labels
    )
    assert handed.awaiting_human and not graph.awaiting_human


def test_created_tickets_are_appended_and_related_to_the_spec() -> None:
    graph = build_graph(tickets=(build_ticket(10),))
    created = build_ticket(11)

    grown = graph.with_created([created])

    assert [ticket.number for ticket in grown.tickets] == [10, 11]
    assert grown.sub_issue_total == 2
    assert grown.relations == (IssueRelation(graph.spec.ref, created.ref),)
    assert graph.relations == ()


def test_a_ticket_of_another_spec_is_not_found() -> None:
    with pytest.raises(ValueError, match="does not belong"):
        build_graph().ticket(99)


def test_a_graph_is_an_immutable_snapshot() -> None:
    graph: SpecGraphSnapshot = build_graph()

    with pytest.raises(FrozenInstanceError):
        setattr(graph, "tickets", ())
