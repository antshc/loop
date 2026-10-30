from __future__ import annotations

import pytest

from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.issues.issue import CloseReason, IssueState
from software_factory.shared.github.issues.readiness import TicketReadiness, TicketReadinessPolicy
from software_factory.shared.github.issues.spec import Ticket
from tests.shared.builders import build_blocker, build_graph, build_issue, build_ticket

READY, BLOCKED, UNKNOWN = TicketReadiness.READY, TicketReadiness.BLOCKED, TicketReadiness.UNKNOWN
CLOSED = IssueState.CLOSED


def _readiness(graph: SpecGraphSnapshot) -> dict[int, TicketReadiness]:
    return TicketReadinessPolicy().evaluate(graph)


@pytest.mark.parametrize(
    ("blockers", "expected"),
    [
        pytest.param((), READY, id="no blockers"),
        pytest.param((build_blocker(10, 20),), BLOCKED, id="open blocker"),
        pytest.param((build_blocker(10, 20, CLOSED, CloseReason.COMPLETED),), READY, id="blocker closed as completed"),
        pytest.param((build_blocker(10, 20, CLOSED),), READY, id="blocker closed with no reason GitHub reports"),
        pytest.param((build_blocker(10, 20, CLOSED, CloseReason.NOT_PLANNED),), BLOCKED, id="blocker closed as not planned"),
        pytest.param((build_blocker(10, 20, CLOSED, CloseReason.DUPLICATE),), BLOCKED, id="blocker closed as duplicate"),
        pytest.param(
            (build_blocker(10, 20, CLOSED, CloseReason.COMPLETED), build_blocker(10, 21)), BLOCKED, id="one blocker still open",
        ),
        pytest.param(
            (build_blocker(10, 20, CLOSED, CloseReason.COMPLETED), build_blocker(10, 21, CLOSED, CloseReason.COMPLETED)),
            READY,
            id="every blocker completed",
        ),
        pytest.param((build_blocker(11, 20),), READY, id="another Ticket's blocker does not count"),
    ],
)
def test_a_ticket_is_ready_only_when_every_blocker_is_done(blockers, expected: TicketReadiness) -> None:
    graph = build_graph(tickets=(build_ticket(10),), blocked_by=blockers)

    assert _readiness(graph) == {10: expected}


def test_a_ticket_whose_dependencies_were_not_read_completely_is_unknown_never_ready() -> None:
    graph = build_graph(tickets=(build_ticket(10), build_ticket(11)), unread_dependencies=(10,))

    assert _readiness(graph) == {10: UNKNOWN, 11: READY}


def test_unread_dependencies_win_over_the_edges_that_were_read() -> None:
    graph = build_graph(
        tickets=(build_ticket(10),),
        blocked_by=(build_blocker(10, 20, CLOSED, CloseReason.COMPLETED),),
        unread_dependencies=(10,),
    )

    assert _readiness(graph) == {10: UNKNOWN}


def test_a_legacy_blocked_by_text_line_is_not_a_blocker() -> None:
    legacy = Ticket.of(build_issue(10, body="Do the thing\n\nBlocked by #20"))

    assert _readiness(build_graph(tickets=(legacy,))) == {10: READY}


def test_every_ticket_of_the_spec_gets_a_readiness() -> None:
    graph = build_graph(tickets=(build_ticket(10), build_ticket(11), build_ticket(12)), blocked_by=(build_blocker(11, 10),))

    assert _readiness(graph) == {10: READY, 11: BLOCKED, 12: READY}


@pytest.mark.parametrize(
    ("ticket", "actionable"),
    [
        pytest.param(build_ticket(10), True, id="open and ready"),
        pytest.param(build_ticket(10, labels=("hitl",)), False, id="hitl"),
        pytest.param(build_ticket(10, labels=("spec",)), False, id="spec"),
        pytest.param(build_ticket(10, state=CLOSED), False, id="closed"),
    ],
)
def test_a_closed_hitl_or_spec_ticket_is_never_offered_whatever_its_readiness(ticket: Ticket, actionable: bool) -> None:
    graph = build_graph(tickets=(ticket,))

    assert _readiness(graph) == {10: READY}
    assert (ticket.number in [offered.number for offered in graph.deliverable_tickets if offered.actionable]) is actionable
