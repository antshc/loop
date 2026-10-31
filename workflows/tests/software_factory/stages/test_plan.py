"""The plan Stages: the read-only planner and the creation of its Tickets, each run as a one-Stage Workflow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from workflows.software_factory.events.specs import SpecHeld
from workflows.software_factory.events.tickets import (
    PlannedTicket,
    PlanProposed,
    TicketsCreated,
)
from workflows.software_factory.stages import plan
from workflows.tests.software_factory.stages.support import (
    DeveloperScript,
    StageRig,
    answers,
)

FORM = {"title": "Add login form", "body": "Build the form", "blocked_by": []}
TESTS = {"title": "Add login tests", "body": "Cover the form", "blocked_by": [0]}
NO_TICKETS = [{"tickets": []}, "the planner returned no Tickets"]


def planning(rig: StageRig, script: DeveloperScript, *stages):
    """The routing Stage, then the planning Stages on the checkout."""
    return rig.in_worktree(
        script, *(stages or (plan.plan_spec(), plan.create_tickets()))
    )


def unplanned(tmp_path: Path) -> StageRig:
    """A Spec with no sub-issues."""
    return StageRig(tmp_path, tickets=[])


def test_a_spec_that_already_has_sub_issues_skips_planning_and_produces_no_event(
    tmp_path: Path,
) -> None:
    """Given a Spec with a Ticket, when the plan Stages run, then both are skipped, the planner is never asked and nothing is written."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM])])

    result = rig.run(planning(rig, script))

    assert result.status == "completed"
    assert rig.skipped() == ["plan-spec", "create-tickets"]
    assert script.requests == [] and rig.gh.writes() == []
    assert rig.history(PlanProposed) == () and rig.history(TicketsCreated) == ()


def test_a_spec_whose_sub_issues_are_all_closed_is_not_replanned(
    tmp_path: Path,
) -> None:
    """Given a Spec whose only Ticket is closed, when the plan Stage runs, then it is skipped: closed work prevents replanning."""
    rig = StageRig(tmp_path)
    rig.gh.close_issue(10)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM])])

    result = rig.run(planning(rig, script))

    assert result.status == "completed" and script.requests == []
    assert rig.skipped() == ["plan-spec", "create-tickets"]


def test_a_target_that_does_not_plan_skips_planning(tmp_path: Path) -> None:
    """Given a routed target with planning disabled, when the Spec has no sub-issues, then the plan Stages are skipped."""
    rig = unplanned(tmp_path)
    rig.target = replace(rig.target, plan=False)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM])])

    result = rig.run(planning(rig, script))

    assert result.status == "completed" and script.requests == []
    assert rig.skipped() == ["plan-spec", "create-tickets"]
    assert rig.gh.writes() == []


def test_a_valid_plan_passes_with_the_proposed_tickets_and_leaves_the_repository_and_tracker_unchanged(
    tmp_path: Path,
) -> None:
    """Given a Spec with no sub-issues and a planner that proposes two Tickets, when the plan Stage runs, then it passes with them and writes nothing."""
    rig = unplanned(tmp_path)
    base = rig.repo.head()
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM, TESTS])])

    result = rig.run(planning(rig, script, plan.plan_spec()))

    assert result.status == "completed"
    assert rig.history(PlanProposed) == (
        PlanProposed(
            (
                PlannedTicket("Add login form", "Build the form"),
                PlannedTicket("Add login tests", "Cover the form", (0,)),
            )
        ),
    )
    assert rig.repo.head() == base and rig.repo.status() == ""
    assert rig.gh.writes() == []
    assert "Checkout: Add login page" in script.requests[0].prompt


def test_a_declining_planner_holds_the_spec_with_its_reason(tmp_path: Path) -> None:
    """Given a planner that declines, when the plan Stage runs, then it holds with the planner's reason and creates nothing."""
    rig = unplanned(tmp_path)
    script = DeveloperScript(
        rig.repo, [answers("failed", reason="the Spec contradicts itself")]
    )

    result = rig.run(planning(rig, script))

    assert (result.status, result.reason) == ("held", "the Spec contradicts itself")
    assert rig.history(SpecHeld) == (SpecHeld("the Spec contradicts itself"),)
    assert rig.gh.writes() == []


@pytest.mark.parametrize(
    ("tickets", "complaint"),
    [
        ([], "the planner returned no Tickets"),
        (
            [{**FORM, "blocked_by": [0]}],
            "is blocked by a Ticket that does not come before it",
        ),
        (
            [FORM, {**TESTS, "blocked_by": [2]}],
            "is blocked by a Ticket that does not come before it",
        ),
        (
            [{**FORM, "title": f"Ticket {number}"} for number in range(16)],
            "the limit is 15",
        ),
    ],
    ids=["no-tickets", "self-blocked", "forward-blocked", "too-many"],
)
def test_a_plan_that_breaks_the_plan_rules_fails_the_stage_and_creates_nothing(
    tmp_path: Path, tickets: list, complaint: str
) -> None:
    """Given a planner whose plan breaks a plan rule, when the plan Stages run, then the Stage fails with the rule as evidence and no Ticket exists."""
    rig = unplanned(tmp_path)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=tickets)])

    result = rig.run(planning(rig, script))

    assert result.status == "failed"
    (held,) = rig.history(SpecHeld)
    assert complaint in held.reason
    assert rig.gh.writes() == [] and rig.skipped() == []


def test_a_planner_that_changes_the_working_tree_fails_the_stage_and_the_checkout_is_restored(
    tmp_path: Path,
) -> None:
    """Given a planner that leaves a file behind, when the plan Stage runs, then the read-only contract fails it and the checkout is clean."""
    rig = unplanned(tmp_path)

    def dirties(repo) -> str:
        repo.write("notes.txt", "plan notes\n")
        return answers("completed", tickets=[FORM])(repo)

    script = DeveloperScript(rig.repo, [dirties])

    result = rig.run(planning(rig, script))

    assert result.status == "failed"
    assert (
        "read-only attempt changed the working tree"
        in rig.spans.attempts("plan-spec")[0].attributes["factory.stage.reason"]
    )
    assert rig.repo.status() == "" and rig.gh.writes() == []


def test_create_tickets_persists_the_plan_comment_then_creates_each_ticket_once_with_its_blocked_by_edges(
    tmp_path: Path,
) -> None:
    """Given a proposed plan, when the Tickets are created, then the plan marker Comment, both Tickets and the native edge exist."""
    rig = unplanned(tmp_path)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM, TESTS])])

    result = rig.run(planning(rig, script))

    assert result.status == "completed"
    assert [title for title, _ in rig.gh.created_issues()] == [
        "Add login form",
        "Add login tests",
    ]
    assert rig.gh.blockers_of(102) == [101] and rig.gh.blockers_of(101) == []
    (plan_comment,) = [body for number, body in rig.gh.posted_comments() if number == 1]
    assert (
        plan_comment.startswith(
            "implement_spec: plan\n\n1. Add login form\n2. Add login tests"
        )
        and "<!-- loop:plan " in plan_comment
    )
    assert rig.history(TicketsCreated) == (TicketsCreated((101, 102)),)


def test_rerunning_after_the_tickets_exist_creates_no_second_ticket_comment_or_edge(
    tmp_path: Path,
) -> None:
    """Given a finished plan, when the Workflow runs again, then the Spec has sub-issues so nothing is planned or created again."""
    rig = unplanned(tmp_path)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM, TESTS])])
    workflow = planning(rig, script)
    rig.run(workflow)
    writes = len(rig.gh.writes())

    result = rig.run(workflow)

    assert result.status == "completed"
    assert len(script.requests) == 1 and len(rig.gh.writes()) == writes
    assert len(rig.gh.created_issues()) == 2 and rig.gh.blockers_of(102) == [101]


def test_a_run_after_a_crash_part_way_through_the_tickets_resumes_the_persisted_plan_without_duplicates(
    tmp_path: Path,
) -> None:
    """Given a run that stopped after the first Ticket, when the Workflow runs again, then the second Ticket is created and nothing is replanned or duplicated."""
    rig = unplanned(tmp_path)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM, TESTS])])
    workflow = planning(rig, script)
    rig.gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues", after=1)

    crashed = rig.run(workflow)
    resumed = rig.run(workflow)

    assert crashed.status == "failed" and resumed.status == "completed"
    assert len(script.requests) == 1
    assert [ticket.title for ticket in rig.issues.spec(1).tickets] == [
        "Add login form",
        "Add login tests",
    ]
    assert rig.gh.blockers_of(102) == [101]
    assert (
        len(
            [
                body
                for number, body in rig.gh.posted_comments()
                if number == 1 and "<!-- loop:plan" in body
            ]
        )
        == 1
    )
    assert rig.skipped() == ["plan-spec"] and rig.history(TicketsCreated) == (
        TicketsCreated((101, 102)),
    )


def test_a_run_after_a_crash_before_the_first_ticket_resumes_from_the_persisted_plan_comment(
    tmp_path: Path,
) -> None:
    """Given a plan Comment on a Spec with no Tickets, when the Workflow runs again, then the planner is not asked and the Tickets are created."""
    rig = unplanned(tmp_path)
    script = DeveloperScript(rig.repo, [answers("completed", tickets=[FORM, TESTS])])
    workflow = planning(rig, script)
    rig.gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues")

    crashed = rig.run(workflow)
    resumed = rig.run(workflow)

    assert crashed.status == "failed" and resumed.status == "completed"
    assert len(script.requests) == 1
    assert [ticket.title for ticket in rig.issues.spec(1).tickets] == [
        "Add login form",
        "Add login tests",
    ]
    assert rig.gh.blockers_of(102) == [101]
    assert rig.history(TicketsCreated) == (TicketsCreated((101, 102)),)


def test_plan_violation_rejects_a_blank_title(tmp_path: Path) -> None:
    """Given a planned Ticket without a title, when the plan is checked, then it is rejected."""
    assert (
        plan.plan_violation([PlannedTicket("  ", "body")])
        == "planned Ticket 0 has no title"
    )
    assert plan.plan_violation([PlannedTicket("Title", "body")]) is None
