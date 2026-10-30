"""Planning in the implement_spec definition: a Spec without Tickets is planned, and a planner that declines or proposes an invalid plan hands the Spec to a person."""

from __future__ import annotations

from pathlib import Path

from .harness import ImplementSpecHarness

FORM = {"title": "Add login form", "body": "Build the form", "blocked_by": []}
TESTS = {"title": "Add login tests", "body": "Cover the form", "blocked_by": [0]}


def test_a_spec_without_tickets_is_planned_and_its_new_tickets_are_delivered_in_dependency_order(tmp_path: Path) -> None:
    """Given a Spec with no Tickets and a planner that proposes two, when the Spec runs, then both are created, delivered in order and the run exits 0."""
    harness = ImplementSpecHarness(tmp_path, tickets=[])
    harness.agents.planned = [FORM, TESTS]

    code = harness.run()

    assert code == 0
    assert [title for title, _ in harness.rig.gh.created_issues()] == ["Add login form", "Add login tests"]
    assert harness.closed() == [101, 102] and harness.agents.developer_runs() == 2
    assert len(harness.agents.prompts["planner"]) == 1


def test_a_planner_that_declines_hands_the_spec_to_a_person_and_creates_no_ticket(tmp_path: Path) -> None:
    """Given a planner that declines, when the Spec runs, then the Spec carries hitl with the reason, no Ticket exists and the run exits 1."""
    harness = ImplementSpecHarness(tmp_path, tickets=[])

    code = harness.run()

    assert code == 1
    assert "hitl" in harness.labels(1) and "needs a product decision" in harness.rig.comments(1)[0]
    assert harness.rig.gh.created_issues() == [] and harness.agents.developer_runs() == 0


def test_an_invalid_plan_hands_the_spec_to_a_person_and_creates_no_ticket(tmp_path: Path) -> None:
    """Given a plan with no Tickets, when the Spec runs, then the Spec is handed to a person and no Ticket is created."""
    harness = ImplementSpecHarness(tmp_path, tickets=[])
    harness.agents.planned = []

    code = harness.run()

    assert code == 1 and "hitl" in harness.labels(1)
    assert "no Tickets" in harness.rig.comments(1)[0] and harness.rig.gh.created_issues() == []


def test_a_spec_that_already_has_tickets_is_not_planned(tmp_path: Path) -> None:
    """Given a Spec with a Ticket, when it runs, then the planner is never asked."""
    harness = ImplementSpecHarness(tmp_path)

    harness.run()

    assert harness.agents.prompts["planner"] == []
