"""The typed agent contract: errors, session pass-through and tuning, shown through the developer role."""

from __future__ import annotations

from pathlib import Path

import pytest

from loop import AgentReplyError, AgentRunError, Declined
from workflows.software_factory.agents.roles.developer import DEVELOPER, Delivered, DeveloperRequest

from .support import ScriptedRunner, envelope, process_failure, run_agent, timeout

REQUEST = DeveloperRequest(
    "Checkout|10", {"number": 10}, (), "ccode(Checkout|10): ", Path("/work/tree"), "main", "feature",
)
COMPLETED = {"commit": "abc", "summary": "done", "verification": "ran tests"}


def test_a_valid_completed_envelope_returns_the_typed_reply() -> None:
    """Given a completed envelope, when the agent runs, then the reply carries every decoded field."""
    runner = ScriptedRunner(envelope(identifier="Checkout|10", **COMPLETED))

    assert run_agent(DEVELOPER, REQUEST, runner) == Delivered("abc", "done", "ran tests")


def test_the_prompt_and_the_session_name_reach_the_agent_request() -> None:
    """Given a session name, when the agent runs, then the CLI request carries the encoded prompt and the name."""
    runner = ScriptedRunner(envelope(**COMPLETED))

    run_agent(DEVELOPER, REQUEST, runner, session="loop(1) Checkout")

    assert runner.requests[0].prompt == DEVELOPER.encode(REQUEST)
    assert runner.requests[0].name == "loop(1) Checkout"


def test_a_failed_envelope_is_a_declined_value_not_an_error() -> None:
    """Given a failed envelope with a reason, when the agent runs, then the reply is Declined with that reason."""
    assert run_agent(DEVELOPER, REQUEST, ScriptedRunner(envelope("failed", reason="stuck"))) == Declined("stuck")


@pytest.mark.parametrize(
    "output",
    [
        "no envelope at all",
        envelope("weird"),
        envelope(commit="abc", summary="only"),
        envelope("failed"),
        envelope(identifier="Checkout|11", **COMPLETED),
    ],
    ids=["no-envelope", "unknown-status", "missing-field", "failed-without-reason", "identifier-mismatch"],
)
def test_an_output_that_does_not_match_the_reply_shape_raises_a_reply_error_naming_the_role(output: str) -> None:
    """Given unusable output, when the agent runs, then a reply error names the developer role."""
    with pytest.raises(AgentReplyError, match="^developer agent: ") as raised:
        run_agent(DEVELOPER, REQUEST, ScriptedRunner(output))

    assert raised.value.role == "developer"


def test_a_response_that_names_another_task_raises_a_reply_error_with_both_identifiers() -> None:
    """Given an echoed identifier that differs from the request's, when the agent runs, then the error shows both."""
    with pytest.raises(AgentReplyError, match=r"'Checkout\|11'.*'Checkout\|10'"):
        run_agent(DEVELOPER, REQUEST, ScriptedRunner(envelope(identifier="Checkout|11", **COMPLETED)))


def test_a_response_without_an_identifier_answers_the_asked_task() -> None:
    """Given an envelope that echoes no identifier, when the agent runs, then it is accepted for the asked task."""
    assert run_agent(DEVELOPER, REQUEST, ScriptedRunner(envelope(**COMPLETED))).commit == "abc"


@pytest.mark.parametrize("error", [process_failure(), timeout(), FileNotFoundError("copilot")], ids=["failed", "timed-out", "missing"])
def test_a_process_that_fails_or_times_out_raises_a_run_error_naming_the_role(error: BaseException) -> None:
    """Given a process error, when the agent runs, then a run error names the developer role."""
    with pytest.raises(AgentRunError, match="^developer agent: ") as raised:
        run_agent(DEVELOPER, REQUEST, ScriptedRunner(error=error))

    assert raised.value.role == "developer"


def test_a_non_zero_stream_exit_code_raises_a_run_error_even_with_a_valid_envelope() -> None:
    """Given a valid envelope on a stream that reported exit code 2, when the agent runs, then a run error is raised."""
    with pytest.raises(AgentRunError, match="exited with code 2"):
        run_agent(DEVELOPER, REQUEST, ScriptedRunner(envelope(**COMPLETED), exit_code=2))


def test_a_tuned_agent_runs_with_the_replaced_profile_field_and_the_original_is_unchanged() -> None:
    """Given a tuned timeout, when both agents run, then only the tuned copy carries it."""
    tuned = DEVELOPER.tuned(timeout_sec=42.0)
    tuned_runner, original_runner = ScriptedRunner(envelope(**COMPLETED)), ScriptedRunner(envelope(**COMPLETED))

    run_agent(tuned, REQUEST, tuned_runner)
    run_agent(DEVELOPER, REQUEST, original_runner)

    assert tuned_runner.profiles[0].timeout_sec == 42.0
    assert original_runner.profiles[0].timeout_sec is None
    assert tuned.role == DEVELOPER.role and tuned.encode(REQUEST) == DEVELOPER.encode(REQUEST)
