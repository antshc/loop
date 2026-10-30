from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from loop import Kind, Phases, Role, WorkflowBuilder


def builder() -> WorkflowBuilder:
    return (
        WorkflowBuilder("Basket delivery")
        .phase("ticket", "Add product", Kind.AGENT, Role.DEVELOPER)
        .phase("verify", "Verify basket", Kind.CODE, Role.CODE)
    )


def test_direct_phases_need_no_builder_and_keep_failed_gates_sticky() -> None:
    """Given direct tracking, when a gate fails, then clean exit cannot erase it."""
    phases = Phases()
    with phases.phase("ticket", Kind.AGENT, Role.DEVELOPER, ticket=43) as phase:
        assert not phase.gate("product added", False, "basket unchanged")
        phase.succeed()
        phase.skip("not needed")
        phase.set(verdict="fail")
    assert phase.status == "failed"
    assert phase.reason == "basket unchanged"
    assert phase.attributes["verdict"] == "fail"


def test_retry_recovery_is_distinct_from_the_run_business_outcome() -> None:
    """Given a rejected attempt, when a retry succeeds, then the caller can finish successfully."""
    workflow = builder().build()
    with workflow.run(spec=42) as run:
        with run.phase("ticket", ticket=43, attempt=1) as first:
            first.fail("no changes")
        with run.phase("ticket", ticket=43, attempt=2) as second:
            second.set(commit="abc")
        run.skip("verify", "handled externally")
        run.set(ready=True)
    assert first.status == "failed"
    assert second.status == "completed"
    assert first.id != second.id
    assert first.parent_id == second.parent_id == run.outcome.id
    assert second.attributes["spec"] == 42
    assert run.outcome.status == "completed"
    assert run.outcome.attributes["ready"] is True
    assert run.executions[-1].status == "skipped"
    assert all(phase.seconds >= 0 for phase in run.executions)


def test_explicit_run_failure_survives_a_successful_child() -> None:
    """Given a business blocker, when final reporting completes, then the run remains failed."""
    with builder().build().run() as run:
        run.fail("PR remains draft")
        with run.phase("verify"):
            pass
    assert run.outcome.status == "failed"
    assert run.outcome.reason == "PR remains draft"


def test_nested_phases_inherit_their_immediate_parent_and_restore_it() -> None:
    """Given nested scopes, when a child closes, then later phases use the original parent."""
    with builder().build().run(spec=42) as run:
        with run.phase("ticket", ticket=43) as ticket:
            with run.phase("verify") as child:
                pass
        with run.phase("verify") as sibling:
            pass
    assert child.parent_id == ticket.id
    assert child.attributes["ticket"] == 43
    assert sibling.parent_id == run.outcome.id
    assert "ticket" not in sibling.attributes


@pytest.mark.parametrize("exception,status", [
    (ValueError("invalid basket"), "failed"),
    (KeyboardInterrupt(), "cancelled"),
    (asyncio.CancelledError(), "cancelled"),
])
def test_exceptions_keep_identity_and_close_phase_and_run(exception: BaseException, status: str) -> None:
    """Given an interrupted body, when it exits, then tracking preserves the original exception."""
    with pytest.raises(type(exception)) as raised:
        with builder().build().run() as run:
            with run.phase("ticket") as phase:
                raise exception
    assert raised.value is exception
    assert phase.status == run.outcome.status == status


def test_workflow_definition_is_frozen_and_runs_do_not_share_state() -> None:
    """Given one definition, when reused, then each run has independent results and identity."""
    source = builder()
    workflow = source.build()
    source.phase("extra", "Extra", Kind.CODE, Role.CODE)
    with workflow.run(spec=1) as first:
        with first.phase("ticket"):
            pass
    with workflow.run(spec=2) as second:
        assert len(second.executions) == 1
        with pytest.raises(ValueError, match="Unknown phase"):
            second.phase("extra")
    assert first.outcome.id != second.outcome.id
    assert second.outcome.attributes["spec"] == 2
    with pytest.raises(FrozenInstanceError):
        workflow.name = "changed"


def test_invalid_declarations_and_use_after_run_raise_clear_errors() -> None:
    """Given invalid usage, when declarations or scopes are opened, then validation explains it."""
    with pytest.raises(ValueError, match="Duplicate phase"):
        builder().phase("ticket", "Duplicate", Kind.CODE, Role.CODE)
    with pytest.raises(ValueError, match="must not be empty"):
        WorkflowBuilder(" ")
    with pytest.raises(ValueError, match="must not be empty"):
        builder().phase("", "Empty", Kind.CODE, Role.CODE)
    with pytest.raises(TypeError, match="span"):
        builder().otel(object())
    with builder().build().run() as run:
        retained = run.phase("ticket")
    with pytest.raises(RuntimeError, match="closed"):
        run.phase("verify")
    with pytest.raises(RuntimeError, match="closed"):
        with retained:
            pass


def test_existing_phase_helpers_can_use_a_builder_run() -> None:
    """Given a helper accepting Phases, when supplied a run, then declared ownership remains consistent."""
    def helper(phases: Phases) -> None:
        with phases.phase("ticket", Kind.AGENT, Role.DEVELOPER, ticket=43) as phase:
            phase.gate("basket changed", True)
    with builder().build().run() as run:
        helper(run)
        with pytest.raises(ValueError, match="owner differs"):
            run.phase("ticket", Kind.AGENT, Role.REVIEWER)
    assert run.executions[1].attributes["ticket"] == 43
    assert run.executions[1].status == "completed"
