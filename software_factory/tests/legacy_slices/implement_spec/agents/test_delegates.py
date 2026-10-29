"""The delegates map each role agent's reply and failures back to the stage-level results and errors."""

from __future__ import annotations

from pathlib import Path

import pytest
from loop import fake_agent

from software_factory.legacy_slices.implement_spec.agents.delegates import (
    chore_pull_request, implement_ticket, plan_spec, review_pull_request,
)
from software_factory.legacy_slices.implement_spec.model.records import (
    AgentCommitSubject, AgentProcessError, ChoreContext, ChoreResult, PlanContext, ReviewContext, TicketContext,
    TicketWorkflowError,
)
from software_factory.tests.agents.support import ScriptedRunner, envelope, process_failure
from software_factory.tests.shared.builders import build_graph, build_ticket


def _chore_context() -> ChoreContext:
    return ChoreContext("https://github.com/o/r/pull/1", "main", "feature", Path("/diff"))


def _review_context() -> ReviewContext:
    return ReviewContext("https://github.com/o/r/pull/1", Path("/checkout"), "Checkout", "abc")


def _ticket_context() -> TicketContext:
    return TicketContext(build_ticket(10, "Add form"), AgentCommitSubject("Checkout", 10), (), "main", "feature")


def _run(delegate, context, runner: ScriptedRunner):
    with fake_agent(runner).open() as workspace:
        return delegate(context, workspace)


def test_a_declined_developer_reply_is_a_failed_ticket_result_with_its_reason() -> None:
    """Given a failed envelope, when the Ticket is implemented, then the result is failed with the reason and no error."""
    result = _run(implement_ticket, _ticket_context(), ScriptedRunner(envelope("failed", reason="stuck")))

    assert (result.identifier, result.status, result.reason) == ("Checkout|10", "failed", "stuck")


def test_a_delivered_developer_reply_is_a_completed_ticket_result() -> None:
    """Given a completed envelope, when the Ticket is implemented, then the result carries commit, summary and verification."""
    output = envelope(commit="abc", summary="done", verification="ran tests")

    result = _run(implement_ticket, _ticket_context(), ScriptedRunner(output))

    assert (result.status, result.commit, result.summary, result.verification) == ("completed", "abc", "done", "ran tests")


def test_an_unreadable_developer_reply_is_a_rejected_response() -> None:
    """Given no envelope, when the Ticket is implemented, then a workflow error that is not a process failure is raised."""
    with pytest.raises(TicketWorkflowError, match="no response object") as raised:
        _run(implement_ticket, _ticket_context(), ScriptedRunner("nothing"))

    assert not isinstance(raised.value, AgentProcessError)


@pytest.mark.parametrize("runner", [ScriptedRunner(error=process_failure()), ScriptedRunner(exit_code=2)], ids=["process-error", "exit-code"])
def test_a_failed_agent_process_is_a_process_error(runner: ScriptedRunner) -> None:
    """Given a failed process or non-zero exit code, when the Ticket is implemented, then a process error is raised."""
    with pytest.raises(AgentProcessError, match="^developer agent: "):
        _run(implement_ticket, _ticket_context(), runner)


@pytest.mark.parametrize("output", ["no json", envelope("failed", reason="x"), envelope(commit_message=3)])
def test_the_best_effort_chore_step_turns_a_declined_or_unreadable_reply_into_an_empty_result(output: str) -> None:
    """Given a declined or unreadable chore reply, when the chore runs, then the result is empty and no error is raised."""
    assert _run(chore_pull_request, _chore_context(), ScriptedRunner(output)) == ChoreResult()


def test_the_chore_step_still_raises_a_process_error_when_the_agent_process_fails() -> None:
    """Given a failed chore process, when the chore runs, then a process error is raised."""
    with pytest.raises(AgentProcessError, match="^chore agent: "):
        _run(chore_pull_request, _chore_context(), ScriptedRunner(error=process_failure()))


def test_a_declined_review_is_a_workflow_error_naming_its_reason() -> None:
    """Given a failed review, when the review runs, then the stage sees a workflow error with the reason."""
    with pytest.raises(TicketWorkflowError, match="review failed: no skill"):
        _run(review_pull_request, _review_context(), ScriptedRunner(envelope("failed", reason="no skill")))


def test_a_completed_review_maps_to_the_review_result() -> None:
    """Given a completed review, when the review runs, then axes, findings, comments and head are carried."""
    output = envelope(
        axes=[{"name": "bugs", "outcome": "completed"}],
        findings=[{"axis": "bugs", "kind": "bug", "summary": "npe"}],
        posted=["c1"],
        head="abc",
    )

    result = _run(review_pull_request, _review_context(), ScriptedRunner(output))

    assert [axis.name for axis in result.axes] == ["bugs"] and result.head_sha == "abc" and result.posted == ("c1",)
    assert [finding.summary for finding in result.blocking_findings] == ["npe"]


def test_a_declined_plan_is_a_failed_plan_result_with_its_reason() -> None:
    """Given a failed planner envelope, when the Spec is planned, then the result is failed with the reason."""
    context = PlanContext(build_graph(labels=("spec", "repo:target:o/r", "repo:base:main")), "main")

    result = _run(plan_spec, context, ScriptedRunner(envelope("failed", reason="ambiguous")))

    assert (result.status, result.reason, result.tickets) == ("failed", "ambiguous", ())
