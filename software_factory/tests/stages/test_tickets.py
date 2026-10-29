"""The Ticket Stages: the Selector, the developer gate and its repository contract, closing, escalating and the delivery check."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from software_factory.events.tickets import Delivery, TicketImplemented, TicketRejected
from software_factory.runtime import Flow, Item, Selector, Stage, handoff, next_, rework
from software_factory.shared.github.issues.spec import Ticket
from software_factory.stages import tickets
from software_factory.tests.runtime.support import NoInput
from software_factory.tests.shared.fakes import issue
from software_factory.tests.stages.support import (
    HITL, DeveloperScript, StageRig, commits_nothing, declines, delivers, probe,
)

PREFIX = "ccode(Checkout|10): "


def ticket_loop(*stages: Stage) -> Flow:
    """A loop over the ready Tickets whose body is `stages`; a failed or escalated Ticket ends the loop, not the run."""
    body = Flow("ticket")
    for stage in stages:
        body = body.stage(stage)
    return Flow("deliver").each(tickets.next_ready_ticket(), body, on_fail=next_())


def test_the_selector_offers_only_open_ready_tickets_in_order_with_a_revision_of_their_body(tmp_path: Path) -> None:
    """Given held, closed and blocked Tickets among ready ones, when the Selector runs, then only the ready actionable ones come, in order."""
    rig = StageRig(
        tmp_path,
        tickets=[
            issue(10, "Ready"), issue(11, "Human", labels=(HITL,)), issue(12, "Split", labels=("spec",)),
            issue(13, "Done", state="CLOSED"), issue(14, "Waits for ten"), issue(15, "Waits for done"), issue(16, "Also ready"),
        ],
        blocked_by={14: [10], 15: [13]},
    )

    offered = tickets.next_ready_ticket().select(tickets.SpecInput(rig.work, rig.issues))

    assert [item.id for item in offered] == ["#10", "#15", "#16"]
    assert offered[0].revision == hashlib.sha256(b"Body of #10").hexdigest()
    assert offered[0].value.title == "Ready"


def test_the_selector_revision_changes_with_the_ticket_body(tmp_path: Path) -> None:
    """Given a Ticket whose body is edited, when the Selector runs again, then the revision differs."""
    rig = StageRig(tmp_path)
    before = tickets.next_ready_ticket().select(tickets.SpecInput(rig.work, rig.issues))[0].revision
    rig.gh._node(10)["body"] = "Changed body"

    after = tickets.next_ready_ticket().select(tickets.SpecInput(rig.work, rig.issues))[0].revision

    assert before != after


def test_a_ticket_delivered_in_exactly_one_validated_commit_passes(tmp_path: Path) -> None:
    """Given a developer that makes one prefixed commit and reports it, when the Ticket is implemented, then it passes."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [delivers(10)])
    seen: list[TicketImplemented] = []
    stage = tickets.implement_ticket()

    result = rig.run(rig.in_worktree(script, flow=ticket_loop(stage, probe(TicketImplemented, seen))))

    assert result.status == "completed"
    assert seen == [TicketImplemented(10, rig.repo.head(), "Added the form.", "pytest passed")]
    assert rig.repo.subjects()[0].startswith(PREFIX)
    assert "Add login form" in script.requests[0].prompt


@pytest.mark.parametrize(
    ("action", "complaint"),
    [
        (commits_nothing(10), "expected exactly one new commit, found 0"),
        (delivers(10, commits=2), "expected exactly one new commit, found 2"),
        (delivers(10, subject="fix: something"), "lacks the required prefix"),
        (delivers(10, report="0" * 40), "is not the branch head"),
    ],
    ids=["no-commit", "two-commits", "wrong-prefix", "reports-another-commit"],
)
def test_an_invalid_ticket_commit_fails_each_attempt_and_restores_the_branch(tmp_path: Path, action, complaint: str) -> None:
    """Given a developer whose commit breaks the contract, when the Ticket is implemented, then both attempts fail and the branch is restored."""
    rig = StageRig(tmp_path)
    base = rig.repo.head()
    script = DeveloperScript(rig.repo, [action])

    result = rig.run(rig.in_worktree(script, flow=Flow("deliver").each(tickets.next_ready_ticket(), Flow("t").stage(tickets.implement_ticket()))))

    assert result.status == "failed"
    assert len(script.requests) == 2
    assert [span.attributes["factory.stage.outcome"] for span in rig.spans.attempts("implement-ticket")] == ["fail", "fail"]
    assert complaint in rig.spans.attempts("implement-ticket")[0].attributes["factory.stage.reason"]
    assert rig.repo.head() == base and rig.repo.status() == ""


def test_a_declining_developer_fails_the_ticket_and_every_attempt_uses_a_fresh_session(tmp_path: Path) -> None:
    """Given a developer that declines, when the Ticket is implemented, then it fails after two attempts, each a fresh unnamed session."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [declines(10)])

    result = rig.run(rig.in_worktree(script, flow=Flow("deliver").each(tickets.next_ready_ticket(), Flow("t").stage(tickets.implement_ticket()))))

    assert result.status == "failed"
    assert [request.name for request in script.requests] == [None, None]
    assert rig.repo.status() == ""


def test_a_ticket_that_fails_once_is_reworked_and_passes_on_the_second_attempt(tmp_path: Path) -> None:
    """Given a developer that first makes no commit, when the Ticket is implemented, then the second attempt passes."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [commits_nothing(10), delivers(10)])

    result = rig.run(rig.in_worktree(script, flow=ticket_loop(tickets.implement_ticket())))

    assert result.status == "completed"
    assert [span.attributes["factory.stage.outcome"] for span in rig.spans.attempts("implement-ticket")] == ["fail", "pass"]


def escalating(max_attempts: int = 1) -> Stage:
    """The developer Stage that hands its Ticket to a person once its attempts are spent."""
    return tickets.implement_ticket().on_fail(rework(max_attempts=max_attempts, then=handoff(tickets.escalate_ticket(label=HITL))))


def pick_ticket(rig: StageRig) -> Selector:
    """A Selector that offers Ticket 10 as it was read before anything changed."""
    ticket = rig.issues.spec(1).tickets[0]
    return Selector("pick-ticket", NoInput, Ticket, lambda _: [Item(ticket, "#10", "r1")])


def implemented(commit: str = "abc1234") -> Stage:
    """A stand-in for the developer Stage."""
    return Stage("implemented", NoInput, produces=TicketImplemented).action(
        lambda _: TicketImplemented(10, commit, "Added the form.", "pytest passed"),
    )


def test_closing_a_ticket_writes_the_delivery_comment_and_a_second_run_adopts_the_closure(tmp_path: Path) -> None:
    """Given a delivered Ticket, when close_ticket runs twice, then it closes once with the commit, summary and verification."""
    rig = StageRig(tmp_path)
    workflow = rig.workflow().each(pick_ticket(rig), Flow("t").stage(implemented()).stage(tickets.close_ticket()))

    first, second = rig.run(workflow), rig.run(workflow)

    assert (first.status, second.status) == ("completed", "completed")
    closes = rig.gh.calls_matching("issue", "close")
    assert len(closes) == 1
    assert closes[0][-1] == "Delivered in abc1234.\n\nAdded the form.\n\npytest passed"


def test_closing_a_ticket_that_is_already_closed_writes_nothing(tmp_path: Path) -> None:
    """Given a Ticket a person closed meanwhile, when close_ticket runs, then it adopts the closure."""
    rig = StageRig(tmp_path)
    workflow = rig.workflow().each(pick_ticket(rig), Flow("t").stage(implemented()).stage(tickets.close_ticket()))
    rig.gh.close_issue(10)

    result = rig.run(workflow)

    assert result.status == "completed"
    assert rig.gh.writes() == []


def test_escalating_a_ticket_labels_the_ticket_and_its_spec_and_says_why_and_writes_nothing_twice(tmp_path: Path) -> None:
    """Given a Ticket the developer keeps declining, when it is escalated twice, then both carry the label and the reason once."""
    rig = StageRig(tmp_path)
    escalate = handoff(tickets.escalate_ticket(label=HITL))
    implement = tickets.implement_ticket().on_fail(rework(max_attempts=2, then=escalate))
    script = DeveloperScript(rig.repo, [declines(10, "the form needs a design")])
    workflow = rig.in_worktree(script, flow=Flow("deliver").each(tickets.next_ready_ticket(), Flow("t").stage(implement), on_fail=next_()))

    rig.run(workflow)
    rig.run(workflow)

    assert rig.labels(10).count(HITL) == 1 and rig.labels(1).count(HITL) == 1
    assert rig.comments(10) == ["implement_spec: the form needs a design"]
    assert rig.comments(1) == ["implement_spec: the form needs a design"]


def test_the_escalate_stage_adopts_an_escalation_that_both_labels_already_show(tmp_path: Path) -> None:
    """Given the Ticket and its Spec already carry the label, when escalate_ticket runs, then nothing is written."""
    rig = StageRig(tmp_path, tickets=[issue(10, "Add login form")])
    rig.gh._apply_issue_write(("issue", "edit", "10", "--add-label", HITL))
    rig.gh._apply_issue_write(("issue", "edit", "1", "--add-label", HITL))
    rejected = Stage("rejected", NoInput, produces=TicketRejected).action(lambda _: TicketRejected(10, "no design"))
    workflow = rig.workflow(rejected).each(pick_ticket(rig), Flow("t").stage(tickets.escalate_ticket(label=HITL)))
    rig.gh.calls.clear()

    rig.run(workflow)

    assert rig.gh.writes() == []


def test_the_delivery_check_fails_after_an_escalation_in_this_run(tmp_path: Path) -> None:
    """Given a Ticket escalated in this run, when the delivery is required, then the run fails."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [declines(10)])
    workflow = rig.in_worktree(script, tickets.require_delivered(), flow=ticket_loop(escalating()))

    result = rig.run(workflow)

    assert result.status == "failed"
    assert "escalated to a person" in (result.reason or "") and "#10" in (result.reason or "")


def test_the_delivery_check_holds_while_tickets_remain_held(tmp_path: Path) -> None:
    """Given a Ticket that waits for a person, when the delivery is required, then the run holds."""
    rig = StageRig(tmp_path, tickets=[issue(10, "Add login form", labels=(HITL,))])
    script = DeveloperScript(rig.repo, [delivers(10)])

    result = rig.run(rig.in_worktree(script, tickets.require_delivered(), flow=ticket_loop(escalating())))

    assert result.status == "held"
    assert "#10" in (result.reason or "")


def test_the_delivery_check_passes_with_the_head_and_commits_once_every_ticket_is_delivered(tmp_path: Path) -> None:
    """Given every Ticket closed with a commit on the feature branch, when the delivery is required, then it passes with them."""
    rig = StageRig(tmp_path, tickets=[issue(10, "Add login form", state="CLOSED")])
    rig.repo.write("done.txt", "done\n")
    head = rig.repo.commit("ccode(Checkout|10): implement")
    seen: list[Delivery] = []
    script = DeveloperScript(rig.repo, [delivers(10)])

    result = rig.run(rig.in_worktree(script, tickets.require_delivered(), probe(Delivery, seen), flow=ticket_loop(escalating())))

    assert result.status == "completed"
    assert seen == [Delivery(head, (f"{head[:7]} ccode(Checkout|10): implement",))]
