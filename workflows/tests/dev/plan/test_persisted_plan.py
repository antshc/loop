from __future__ import annotations

from dataclasses import replace

from workflows.dev.plan.planner import _plan_comment, persisted_plan, unfinished_plan
from workflows.shared.github.identity import CommentRef, IssueRef
from workflows.shared.github.issues.issue import Comment
from workflows.shared.github.issues.ticketing import TicketDraft
from workflows.tests.shared.builders import REPOSITORY, build_graph

_DRAFTS = (
    TicketDraft("0-basket", "Add basket", "Create a basket --> <!-- not the end -->"),
    TicketDraft("1-coffee", "Add coffee", "Add a product", blocked_by=(0,)),
)


def _spec_with_plan(*, linked: int, plan: str | None = None):
    comment = Comment(CommentRef(IssueRef(REPOSITORY, 1), 1), "loop", plan or _plan_comment(_DRAFTS), "2026-01-01T00:00:00Z")
    graph = build_graph()
    return replace(graph, spec=replace(graph.spec, comments=(comment,)), sub_issue_total=linked)


def test_a_plan_kept_on_the_spec_reads_back_exactly_even_when_a_body_looks_like_markup() -> None:
    assert persisted_plan(_spec_with_plan(linked=0)) == _DRAFTS


def test_a_spec_without_a_plan_comment_or_with_an_unreadable_one_has_no_persisted_plan() -> None:
    assert persisted_plan(build_graph()) is None
    assert persisted_plan(_spec_with_plan(linked=0, plan="<!-- loop:plan {not json} -->")) is None


def test_a_plan_is_unfinished_only_while_fewer_tickets_are_linked_than_it_plans() -> None:
    assert unfinished_plan(_spec_with_plan(linked=0)) == _DRAFTS
    assert unfinished_plan(_spec_with_plan(linked=1)) == _DRAFTS
    assert unfinished_plan(_spec_with_plan(linked=2)) is None
    assert unfinished_plan(build_graph()) is None
