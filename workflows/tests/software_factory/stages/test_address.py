"""The address Stages: the grouping coverage gate, the resolution gate table, the replay of decided work, and the closing clarification check."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.software_factory.agents.roles.address import FeedbackIssue, FeedbackReply, Grouped, Resolved, ResolveRequest
from workflows.software_factory.events.feedback import (
    FeedbackBatch, FeedbackGrouped, GroupedIssue, IssueResolved, IssueUnclear, ReplyDraft,
)
from workflows.software_factory.events.pull_requests import PullRequestPolled
from workflows.software_factory.events.specs import RepositoryTarget
from loop import Evidence, Fail, Hold, Item, Pass
from workflows.software_factory.stages.address import (
    CLARIFICATION_REQUESTED, GroupInput, IssueWork, UnclearInput, group_feedback, issues, require_no_unclear, resolve_issue,
)

ITEMS = tuple({"key": f"k{n}", "kind": "thread", "status": "pending"} for n in (1, 2))
BATCH = FeedbackBatch("head", False, ITEMS)
POLLED = PullRequestPolled(RepositoryTarget("o/r", Path("/w"), Path("/t")), 7, {"pr": {"head": {"sha": "head", "ref": "feature"}}})
UNCHANGED, CHANGED = Evidence("b", "b", False), Evidence("b", "b", True)


def work(issue: GroupedIssue = GroupedIssue("a", "s", ("k1",)), *, resolved=(), unclear=()) -> IssueWork:
    return IssueWork(issue, BATCH, POLLED, tuple(resolved), tuple(unclear))


def resolution(verdict: str, *, evidence: tuple[str, ...] = ("cart.py:4",), replies: dict[str, str] | None = None) -> Resolved:
    drafted = {"k1": "Thanks"} if replies is None else replies
    return Resolved(verdict, evidence, tuple(FeedbackReply(key, body) for key, body in drafted.items()), "because")


def grouped(*issues: tuple[str, tuple[str, ...]]) -> Grouped:
    return Grouped(tuple(FeedbackIssue(id, "s", keys) for id, keys in issues))


GROUPING = {
    "an empty grouping": (grouped(), "Address returned no issues"),
    "an invalid issue id": (grouped(("not valid!", ("k1", "k2"))), "Address returned an invalid issue"),
    "an issue without keys": (grouped(("a", ("k1", "k2")), ("b", ())), "Address returned an invalid issue"),
    "an item left uncovered": (grouped(("a", ("k1",))), "Address must assign every feedback item exactly once"),
    "an item covered twice": (grouped(("a", ("k1", "k2")), ("b", ("k2",))), "Address must assign every feedback item exactly once"),
    "an unknown item": (grouped(("a", ("k1", "k2", "k3"))), "Address must assign every feedback item exactly once"),
    "a repeated issue id": (grouped(("a", ("k1",)), ("a", ("k2",))), "Address must assign every feedback item exactly once"),
}


@pytest.mark.parametrize("reply,reason", GROUPING.values(), ids=GROUPING.keys())
def test_a_grouping_that_does_not_cover_every_item_exactly_once_fails(reply: Grouped, reason: str) -> None:
    """Given a grouping with a coverage problem, when gated, then group-feedback fails with that reason."""
    stage = group_feedback()

    assert stage.judge(GroupInput(BATCH, ()), reply, UNCHANGED) == Fail(reason)


def test_a_complete_grouping_passes_with_the_issues_the_batch_token_and_the_head_it_opened_at() -> None:
    """Given a grouping covering each item once, when gated, then it passes with the grouped issues."""
    stage = group_feedback()

    verdict = stage.judge(GroupInput(BATCH, ()), grouped(("a", ("k2",)), ("b", ("k1",))), UNCHANGED)

    assert verdict == Pass(FeedbackGrouped(BATCH.token, "head", (GroupedIssue("a", "s", ("k2",)), GroupedIssue("b", "s", ("k1",)))))


def test_grouping_is_skipped_only_when_this_batch_was_already_grouped() -> None:
    """Given a recorded grouping, when the Stage's condition is asked for the same and for another batch, then only the same batch skips."""
    stage = group_feedback()
    recorded = FeedbackGrouped(BATCH.token, "head", ())
    other = FeedbackBatch("head", False, ITEMS[:1])

    assert [stage.when_(GroupInput(batch, (recorded,))) for batch in (BATCH, other)] == [False, True]


def test_the_issues_are_selected_in_order_with_the_head_the_batch_opened_at_as_revision() -> None:
    """Given the latest grouping of the batch, when the issues are selected, then each item carries its issue, its id and the base as revision."""
    first = GroupedIssue("a", "s", ("k1",))
    second = GroupedIssue("b", "s", ("k2",))
    older = FeedbackGrouped(BATCH.token, "old", (second,))
    latest = FeedbackGrouped(BATCH.token, "head", (first, second))

    selector = issues()

    assert selector.select(GroupInput(BATCH, (older, latest))) == [Item(first, "a", "head"), Item(second, "b", "head")]
    assert selector.select(GroupInput(BATCH, ())) == []


GATES = {
    "a fix with changes passes": ("fix-needed", CHANGED, {}, "pass"),
    "no change needed without changes passes": ("no-change-needed", UNCHANGED, {}, "pass"),
    "unclear without changes holds": ("unclear", UNCHANGED, {"evidence": ()}, "hold"),
    "a fix without changes fails": ("fix-needed", UNCHANGED, {}, "Agent claimed a fix but produced no changes"),
    "no change needed with changes fails": ("no-change-needed", CHANGED, {}, "Agent reported no change needed but changed files"),
    "unclear with changes fails": ("unclear", CHANGED, {}, "Agent reported unclear but changed files"),
    "a fix without path:line evidence fails": ("fix-needed", CHANGED, {"evidence": ("looks fine",)}, "Address verdict requires path:line evidence"),
    "a missing reply fails": ("fix-needed", CHANGED, {"replies": {}}, "Address must draft one reply for every feedback item"),
    "a blank reply fails": ("fix-needed", CHANGED, {"replies": {"k1": "  "}}, "Address must draft one reply for every feedback item"),
    "a reply to another item fails": ("fix-needed", CHANGED, {"replies": {"k1": "ok", "k2": "ok"}}, "Address must draft one reply for every feedback item"),
}


@pytest.mark.parametrize("verdict,evidence,options,expected", GATES.values(), ids=GATES.keys())
def test_the_resolution_gate_trusts_the_evidence_the_runtime_collected_not_the_agents_claim(verdict, evidence, options, expected) -> None:
    """Given an agent verdict and what the worktree shows, when gated, then the outcome follows the gate table."""
    stage = resolve_issue(message=lambda _: "m", verify=lambda _: None)
    reply = resolution(verdict, **options)

    outcome = stage.judge(work(), reply, evidence)

    drafts = (ReplyDraft("k1", "Thanks"),)
    if expected == "pass":
        assert outcome == Pass(IssueResolved(BATCH.token, "a", verdict, drafts))
    elif expected == "hold":
        assert outcome == Hold(CLARIFICATION_REQUESTED, IssueUnclear(BATCH.token, "a", "because", drafts))
    else:
        assert outcome == Fail(expected)


def test_an_issue_an_earlier_run_resolved_or_found_unclear_is_skipped_and_another_is_not() -> None:
    """Given a resolved and an unclear issue of this batch and a resolved issue of another batch, when the condition is asked, then only the decided ones of this batch skip."""
    stage = resolve_issue(message=lambda _: "m", verify=lambda _: None)
    resolved = IssueResolved(BATCH.token, "a", "fix-needed", ())
    unclear = IssueUnclear(BATCH.token, "b", "why", ())
    elsewhere = IssueResolved("other", "c", "fix-needed", ())

    asked = [
        stage.when_(work(GroupedIssue("a", None, ("k1",)), resolved=(resolved, elsewhere), unclear=(unclear,))),
        stage.when_(work(GroupedIssue("b", None, ("k2",)), resolved=(resolved, elsewhere), unclear=(unclear,))),
        stage.when_(work(GroupedIssue("c", None, ("k1",)), resolved=(resolved, elsewhere), unclear=(unclear,))),
    ]

    assert asked == [False, False, True]


def test_the_resolve_request_shows_only_the_issue_and_its_own_items_verbatim() -> None:
    """Given an issue covering one of two items, when its request is built, then it carries that issue and that item as stored."""
    stage = resolve_issue(message=lambda _: "m", verify=lambda _: None)

    request = stage.executor.request(work(GroupedIssue("a", None, ("k2",))), Path("/w"))

    assert request == ResolveRequest(FeedbackIssue("a", None, ("k2",)), [ITEMS[1]])


def test_clarification_is_required_only_for_this_batch() -> None:
    """Given an unclear issue of this batch and one of another, when checked, then only this batch's holds."""
    stage = require_no_unclear()
    mine, theirs = IssueUnclear(BATCH.token, "a", "why", ()), IssueUnclear("other", "b", "why", ())

    def verdict(*events: IssueUnclear):
        work = UnclearInput(BATCH, tuple(events))
        return stage.judge(work, stage.executor.run(work), UNCHANGED)

    assert isinstance(verdict(theirs), Pass)
    assert verdict(mine, theirs) == Hold(CLARIFICATION_REQUESTED)
