from __future__ import annotations

import pytest

from software_factory.shared.github.identity import CommentRef, IssueIdentity, IssueRef, RepositoryId
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.issues.errors import IssueError
from software_factory.shared.github.issues.issue import CloseReason, GapState, IssueState
from software_factory.shared.github.issues.issue_classifier import LabelTypeDisagreement
from software_factory.shared.github.issues.readiness import TicketReadiness
from software_factory.shared.github.repo import GhRunner, GitHubRepo
from software_factory.shared.process import CommandError
from software_factory.tests.shared.fakes import FakeGhCli, issue

_ROUTING = ("spec", "repo:target:o/r", "repo:base:main")
_PAGE = "?per_page=100"


def _issues(gh: GhRunner) -> Issues:
    return Issues(GitHubRepo("owner", "repo", gh=gh))


def _graph(gh: FakeGhCli):
    [graph] = _issues(gh).specs()
    return graph


def _read(endpoint: str) -> tuple[str, ...]:
    return ("api", "--paginate", "--slurp", f"repos/owner/repo/issues/{endpoint}")


def test_every_ticket_of_a_spec_is_returned_when_they_span_several_pages() -> None:
    tickets = [issue(number, f"Ticket {number}") for number in range(10, 15)]
    gh = FakeGhCli(specs=[issue(1, "Big spec", labels=_ROUTING)], tickets={1: tickets}, page_size=2)

    graph = _graph(gh)

    assert [ticket.number for ticket in graph.tickets] == [10, 11, 12, 13, 14]
    assert graph.sub_issue_total == 5 and graph.complete


def test_every_comment_and_label_of_an_issue_is_returned_when_they_span_several_pages() -> None:
    labels = ("spec", "a", "b", "c", "d")
    comments = tuple(f"comment {index}" for index in range(5))
    gh = FakeGhCli(specs=[issue(1, "Busy spec", labels=labels, comments=comments)], tickets={1: []}, page_size=2)

    spec = _graph(gh).spec

    assert spec.labels == labels
    assert [comment.body for comment in spec.comments] == list(comments)
    assert spec.complete


def test_an_issue_carries_its_number_stable_identity_title_body_state_labels_and_assignees() -> None:
    node = issue(7, "Fix it", labels=("bug",), assignees=("alice", "bob"), state="closed", close_reason="not_planned")
    gh = FakeGhCli(specs=[node], tickets={})

    read = _issues(gh).issue(7)

    assert read.ref == IssueRef(RepositoryId("owner", "repo"), 7) and read.number == 7
    assert read.identity == IssueIdentity(1007, "I_kwDO7")
    assert (read.title, read.body, read.labels, read.assignees) == ("Fix it", "Body of #7", ("bug",), ("alice", "bob"))
    assert (read.state, read.close_reason) == (IssueState.CLOSED, CloseReason.NOT_PLANNED)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [("completed", CloseReason.COMPLETED), ("duplicate", CloseReason.DUPLICATE), (None, None), ("reopened", None)],
)
def test_the_close_reason_is_read_from_a_closed_issue_only(reason: str | None, expected: CloseReason | None) -> None:
    gh = FakeGhCli(specs=[issue(7, "Done", state="closed", close_reason=reason)], tickets={})

    assert _issues(gh).issue(7).close_reason == expected


def test_an_open_issue_has_no_close_reason() -> None:
    gh = FakeGhCli(specs=[issue(7, "Open", close_reason="reopened")], tickets={})

    assert _issues(gh).issue(7).close_reason is None


def test_a_comment_is_addressed_by_its_issue_and_its_own_id_and_attributed_to_ghost_for_a_deleted_account() -> None:
    node = issue(7, "Chat", comments=("hi",))
    node["comment_list"][0]["user"] = None
    gh = FakeGhCli(specs=[node], tickets={})

    [comment] = _issues(gh).issue(7).comments

    assert comment.ref == CommentRef(IssueRef(RepositoryId("owner", "repo"), 7), 7001)
    assert (comment.author, comment.body, comment.created_at) == ("ghost", "hi", "2026-01-01T00:00:00Z")


def test_the_native_issue_type_is_hydrated_when_github_provides_one() -> None:
    gh = FakeGhCli(specs=[issue(1, "Typed", labels=_ROUTING, issue_type="Spec")], tickets={1: []})

    assert _graph(gh).spec.issue_type == "Spec"
    assert _graph(gh).disagreement is None


def test_classification_succeeds_from_labels_alone_when_there_is_no_native_type() -> None:
    gh = FakeGhCli(specs=[issue(1, "Untyped", labels=_ROUTING)], tickets={1: []})

    graph = _graph(gh)

    assert graph.spec.issue_type is None and graph.number == 1 and graph.disagreement is None


def test_a_label_and_native_type_that_disagree_are_reported_and_the_label_decides() -> None:
    gh = FakeGhCli(specs=[issue(1, "Mixed", labels=_ROUTING, issue_type="Task")], tickets={1: []})

    graph = _graph(gh)

    assert graph.number == 1
    assert graph.disagreement == LabelTypeDisagreement(True, "Task")


def test_only_open_issues_labeled_spec_are_specs() -> None:
    gh = FakeGhCli(
        specs=[
            issue(1, "Open spec", labels=_ROUTING),
            issue(2, "Closed spec", labels=_ROUTING, state="closed"),
            issue(3, "Plain issue", labels=("bug",)),
        ],
        tickets={},
    )

    assert [graph.number for graph in _issues(gh).specs()] == [1]


def test_one_spec_is_read_without_listing_or_loading_the_others() -> None:
    gh = FakeGhCli(
        specs=[issue(1, "First", labels=_ROUTING), issue(2, "Second", labels=_ROUTING, comments=("hi",))],
        tickets={1: [issue(10, "Ticket")], 2: [issue(20, "Other ticket")]},
    )

    [graph] = _issues(gh).specs(2)

    assert graph.number == 2 and [ticket.number for ticket in graph.tickets] == [20]
    assert not any(call[-1].startswith(("repos/owner/repo/issues?", "repos/owner/repo/issues/1/", "repos/owner/repo/issues/10/")) for call in gh.calls)


@pytest.mark.parametrize(
    "node",
    [
        issue(2, "Closed", labels=_ROUTING, state="closed"),
        issue(2, "Plain", labels=("bug",)),
        issue(2, "Pull", labels=_ROUTING, pull_request=True),
    ],
)
def test_asking_for_one_spec_returns_nothing_when_that_issue_is_not_an_open_spec(node: dict) -> None:
    gh = FakeGhCli(specs=[node], tickets={})

    assert _issues(gh).specs(2) == ()


def test_asking_for_a_spec_that_does_not_exist_returns_nothing() -> None:
    assert _issues(FakeGhCli(specs=[], tickets={})).specs(9) == ()


def test_the_comments_of_an_issue_that_reports_none_are_not_requested() -> None:
    gh = FakeGhCli(specs=[issue(1, "Quiet", labels=_ROUTING)], tickets={1: [issue(10, "Quiet ticket")]})

    graph = _graph(gh)

    assert graph.complete and graph.comments == ()
    assert not [call for call in gh.calls if call[-1].split("?")[0].endswith("/comments")]


def test_an_issue_comment_keeps_its_rest_payload_and_reads_a_null_body_as_empty() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING, comments=("hello",))], tickets={1: []})
    node = gh._specs[0]["comment_list"][0]
    node["body"] = None

    [comment] = _graph(gh).spec.comments

    assert comment.body == "" and comment.payload["user"]["login"] == "alice"


def test_a_spec_exposes_its_initiative_target_and_base_branch_from_its_routing_labels() -> None:
    gh = FakeGhCli(specs=[issue(1, "Checkout: Add login", labels=("spec", "repo:target:o/r", "repo:base:release/2.4"))], tickets={1: []})

    graph = _graph(gh)

    assert (graph.initiative, graph.bare_title, graph.target, graph.base_branch) == ("Checkout", "Add login", "o/r", "release/2.4")


def test_each_open_sub_issue_is_a_ticket_of_the_spec_and_closed_ones_are_only_counted() -> None:
    gh = FakeGhCli(
        specs=[issue(1, "Spec", labels=_ROUTING)],
        tickets={1: [issue(10, "Open"), issue(11, "Held", labels=("hitl",)), issue(12, "Done", state="closed")]},
    )

    graph = _graph(gh)

    assert [ticket.number for ticket in graph.tickets] == [10, 11]
    assert graph.sub_issue_total == 3 and graph.held_tickets == (11,)
    assert [(relation.parent.number, relation.child.number) for relation in graph.relations] == [(1, 10), (1, 11)]
    assert not graph.needs_plan


def test_pull_requests_returned_by_an_issue_listing_are_not_specs_or_tickets() -> None:
    gh = FakeGhCli(
        specs=[issue(1, "Spec", labels=_ROUTING), issue(2, "A pull request", labels=_ROUTING, pull_request=True)],
        tickets={1: [issue(10, "Ticket"), issue(11, "Another pull request", pull_request=True)]},
    )

    graph = _graph(gh)

    assert [ticket.number for ticket in graph.tickets] == [10]
    assert graph.sub_issue_total == 1 and graph.complete
    assert [spec.number for spec in _issues(gh).specs()] == [1]


def test_a_pull_request_cannot_be_read_as_an_issue() -> None:
    gh = FakeGhCli(specs=[issue(2, "A pull request", pull_request=True)], tickets={})

    with pytest.raises(IssueError, match="pull request"):
        _issues(gh).issue(2)


def test_a_failed_page_marks_the_read_incomplete() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING, comments=("hi",))], tickets={1: [issue(10, "Ticket")]})
    gh.fail_on(*_read(f"1/comments{_PAGE}"))

    graph = _graph(gh)

    assert not graph.complete and not graph.spec.complete
    assert [(gap.part, gap.state) for gap in graph.spec.gaps] == [("comments", GapState.INCOMPLETE)]
    assert graph.spec.comments == ()


def test_an_unreadable_sub_issue_listing_marks_the_graph_incomplete_and_never_needs_a_plan() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING)], tickets={1: [issue(10, "Ticket")]})
    gh.fail_on(*_read(f"1/sub_issues{_PAGE}"))

    graph = _graph(gh)

    assert not graph.complete and graph.tickets == ()
    assert [(gap.part, gap.state) for gap in graph.gaps] == [("sub_issues", GapState.INCOMPLETE)]
    assert not graph.needs_plan


def test_a_malformed_page_marks_the_read_incomplete() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING)], tickets={1: []})
    real = gh.__call__

    def garbled(args: tuple[str, ...]) -> str:
        return "not json" if args[-1].endswith(f"1/labels{_PAGE}") else real(args)

    graph = _issues(garbled).specs()[0]

    assert [gap.part for gap in graph.spec.gaps] == ["labels"] and not graph.complete


def test_comments_cut_short_of_githubs_own_count_mark_the_read_incomplete() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING, comments=("one", "two"), comment_count=5)], tickets={1: []})

    graph = _graph(gh)

    assert [(gap.part, gap.state, gap.reason) for gap in graph.spec.gaps] == [("comments", GapState.INCOMPLETE, "read 2 of 5")]
    assert not graph.complete


def test_sub_issues_cut_short_of_githubs_own_count_mark_the_read_incomplete() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING, sub_issue_total=4)], tickets={1: [issue(10, "Ticket")]})

    graph = _graph(gh)

    assert [(gap.part, gap.reason) for gap in graph.gaps] == [("sub_issues", "read 1 of 4")]
    assert not graph.needs_plan and not graph.complete


def test_a_permission_denial_marks_that_state_unknown_not_empty() -> None:
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING)], tickets={1: [issue(10, "Ticket")]})
    gh.deny_on(*_read(f"1/sub_issues{_PAGE}"))
    gh.deny_on(*_read(f"1/labels{_PAGE}"))

    graph = _graph(gh)

    assert [(gap.part, gap.state) for gap in graph.gaps] == [("sub_issues", GapState.UNKNOWN)]
    assert [(gap.part, gap.state) for gap in graph.spec.gaps] == [("labels", GapState.UNKNOWN)]
    assert graph.spec.labels == _ROUTING
    assert not graph.complete and not graph.needs_plan


def test_a_spec_listing_that_cannot_be_read_raises_instead_of_returning_no_specs() -> None:
    gh = FakeGhCli()
    gh.deny_on("api", "--paginate", "--slurp", "repos/owner/repo/issues?state=open&labels=spec&per_page=100")

    with pytest.raises(CommandError, match="HTTP 403"):
        _issues(gh).specs()


def _blocked_graph(*, blocker: dict, **options):
    gh = FakeGhCli(
        specs=[issue(1, "Spec", labels=_ROUTING)],
        tickets={1: [issue(10, "Waits")], 2: [blocker]},
        blocked_by={10: [blocker["number"]]},
        **options,
    )
    return gh, _graph(gh)


def test_a_tickets_native_blockers_are_read_with_their_state_and_close_reason() -> None:
    gh = FakeGhCli(
        specs=[issue(1, "Spec", labels=_ROUTING)],
        tickets={1: [issue(10, "Waits")], 2: [issue(20, "Open"), issue(21, "Dropped", state="closed", close_reason="not_planned")]},
        blocked_by={10: [20, 21]},
    )

    graph = _graph(gh)

    assert [(edge.ticket.number, edge.blocker.number, edge.state, edge.close_reason) for edge in graph.blocked_by] == [
        (10, 20, IssueState.OPEN, None),
        (10, 21, IssueState.CLOSED, CloseReason.NOT_PLANNED),
    ]
    assert graph.complete and graph.unread_dependencies == ()


@pytest.mark.parametrize(
    ("blocker", "expected"),
    [
        (issue(20, "Blocker"), TicketReadiness.BLOCKED),
        (issue(20, "Blocker", state="closed", close_reason="completed"), TicketReadiness.READY),
        (issue(20, "Blocker", state="closed", close_reason="not_planned"), TicketReadiness.BLOCKED),
        (issue(20, "Blocker", state="closed", close_reason="duplicate"), TicketReadiness.BLOCKED),
    ],
)
def test_a_ticket_is_ready_blocked_or_held_by_how_its_blocker_ended(blocker: dict, expected: TicketReadiness) -> None:
    _, graph = _blocked_graph(blocker=blocker)

    assert graph.readiness == {10: expected}


def test_blockers_are_read_across_every_page() -> None:
    blockers = [issue(number, f"Blocker {number}", state="closed", close_reason="completed") for number in (20, 21, 22)]
    gh = FakeGhCli(
        specs=[issue(1, "Spec", labels=_ROUTING)],
        tickets={1: [issue(10, "Waits")], 2: blockers},
        blocked_by={10: [20, 21, 22]},
        page_size=1,
    )

    graph = _graph(gh)

    assert [edge.blocker.number for edge in graph.blocked_by] == [20, 21, 22]
    assert graph.readiness == {10: TicketReadiness.READY}


def test_a_ticket_with_only_a_legacy_blocked_by_line_in_its_body_has_no_blockers() -> None:
    legacy = {**issue(10, "Legacy"), "body": "Do it\n\nBlocked by #20"}
    gh = FakeGhCli(specs=[issue(1, "Spec", labels=_ROUTING)], tickets={1: [legacy], 2: [issue(20, "Open")]})

    graph = _graph(gh)

    assert graph.blocked_by == () and graph.readiness == {10: TicketReadiness.READY}


def test_denied_dependencies_make_the_ticket_unknown_without_making_the_spec_unreadable() -> None:
    gh, _ = _blocked_graph(blocker=issue(20, "Blocker", state="closed", close_reason="completed"))
    gh.deny_on(*_read(f"10/dependencies/blocked_by{_PAGE}"))

    graph = _graph(gh)

    assert graph.unread_dependencies == (10,) and graph.blocked_by == ()
    assert graph.readiness == {10: TicketReadiness.UNKNOWN}
    assert graph.complete and graph.held_tickets == (10,) and not graph.has_work


def test_a_failed_dependency_page_makes_the_ticket_unknown() -> None:
    gh, _ = _blocked_graph(blocker=issue(20, "Blocker", state="closed", close_reason="completed"))
    gh.fail_on(*_read(f"10/dependencies/blocked_by{_PAGE}"))

    graph = _graph(gh)

    assert graph.readiness == {10: TicketReadiness.UNKNOWN}


def test_blockers_cut_short_of_githubs_own_count_make_the_ticket_unknown() -> None:
    gh, _ = _blocked_graph(blocker=issue(20, "Blocker", state="closed", close_reason="completed"))
    real = gh.__call__

    def cut_short(args: tuple[str, ...]) -> str:
        output = real(args)
        return output.replace('"total_blocked_by": 1', '"total_blocked_by": 3') if args[-1].endswith(f"issues/1/sub_issues{_PAGE}") else output

    graph = _issues(cut_short).specs()[0]

    assert graph.readiness == {10: TicketReadiness.UNKNOWN}
