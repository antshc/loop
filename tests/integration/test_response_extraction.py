"""Integration tests: the real Copilot output parser decides a run's outcome from its event stream.

Only Git, GitHub, the Copilot CLI process, and the execution store are stand-ins
(docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

from pathlib import Path

from loop import InMemorySessionStore, NoSandbox, copilot
from loop.testing import FakeCopilotCli

from workflow_harness import (
    DevHarness,
    RecordingExecutor,
    _only_worktree,
    copilot_event_frames,
    copilot_event_frames_with_leading_noise,
    copilot_event_frames_with_malformed_response,
    copilot_event_frames_without_response,
)

_IDENTIFIER = "Checkout|10"
_RESULT = {"commit": "", "summary": "done", "verification": "ran tests"}


def _run_with_real_agent(harness: DevHarness, fake_cli: FakeCopilotCli) -> tuple[int, RecordingExecutor]:
    recorder = RecordingExecutor(fake_cli)
    code = harness.run(
        agent_factory=copilot(InMemorySessionStore()),
        sandbox_factory=lambda workspace, cancel: NoSandbox(workspace, executor=recorder, cancel=cancel),
    )
    return code, recorder


def _closed(harness: DevHarness) -> list[tuple[str, ...]]:
    return [call for call in harness.gh.calls if call[:2] == ("issue", "close")]


def _hitl_labelled(harness: DevHarness) -> set[str]:
    return {call[2] for call in harness.gh.calls if call[:2] == ("issue", "edit") and call[-1] == "hitl"}


def test_an_earlier_unrelated_json_object_is_superseded_by_the_later_response_and_the_ticket_closes(
    tmp_path: Path,
) -> None:
    """Given a stream with an unrelated completed envelope before the real response, when the run ends, then
    the last object's identifier, status, and result decide the outcome and the Ticket closes on it."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return copilot_event_frames_with_leading_noise(_IDENTIFIER, "completed", {**_RESULT, "commit": commit})

    code, recorder = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 0
    assert recorder.lines and not recorder.terminated
    assert [call[2] for call in _closed(harness)] == ["10"]


def test_a_response_split_across_many_events_is_reassembled_and_the_ticket_closes(tmp_path: Path) -> None:
    """Given the response envelope arrives split across many delta events, when the run ends, then it is
    reassembled and read as one object and the Ticket closes."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return copilot_event_frames(_IDENTIFIER, "completed", {**_RESULT, "commit": commit})

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 0
    assert [call[2] for call in _closed(harness)] == ["10"]


def test_no_response_object_in_the_output_is_an_error_and_the_ticket_is_not_closed(tmp_path: Path) -> None:
    """Given the output never carries an identifier/status/result object, when the run ends, then it is an
    error, the Ticket is not closed, and it is escalated to a human."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        return copilot_event_frames_without_response()

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 1
    assert _closed(harness) == []
    assert _hitl_labelled(harness) == {"1", "10"}


def test_malformed_json_in_place_of_the_response_is_an_error_and_the_ticket_is_not_closed(tmp_path: Path) -> None:
    """Given the response is truncated mid-object so no balanced JSON object can be parsed, when the run ends,
    then it is an error and the Ticket is not closed."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        return copilot_event_frames_with_malformed_response(_IDENTIFIER, "completed", _RESULT)

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 1
    assert _closed(harness) == []
    assert _hitl_labelled(harness) == {"1", "10"}


def test_a_response_reporting_status_failed_is_an_error_and_the_ticket_is_not_closed(tmp_path: Path) -> None:
    """Given the response envelope reports status `failed` with a reason, when the run ends, then it is an
    error and the Ticket is not closed."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        return copilot_event_frames(_IDENTIFIER, "failed", {"reason": "the acceptance tests do not pass"})

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 1
    assert _closed(harness) == []
    assert _hitl_labelled(harness) == {"1", "10"}


def test_a_completed_response_naming_another_tickets_identifier_is_a_failure_and_the_ticket_is_not_closed(
    tmp_path: Path,
) -> None:
    """Given the response names another Ticket's identifier, when the run ends, then it is a failure even
    though the agent reports `completed`, and this Ticket is not closed."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), "ccode(Checkout|10): work")
        return copilot_event_frames("Checkout|11", "completed", {**_RESULT, "commit": commit})

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 1
    assert _closed(harness) == []
    assert _hitl_labelled(harness) == {"1", "10"}


def test_events_trailing_the_response_all_reach_the_live_output_callback_and_the_process_is_not_terminated_early(
    tmp_path: Path,
) -> None:
    """Given the agent keeps emitting events after printing its response, when the run ends, then every line
    reached the live-output callback, nothing signalled termination, and the Ticket is still closed."""
    harness = DevHarness(tmp_path)
    frames: list[str] = []

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        frames.extend(copilot_event_frames(_IDENTIFIER, "completed", {**_RESULT, "commit": commit}))
        return frames

    code, recorder = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 0
    assert recorder.lines == frames
    assert not recorder.terminated
    assert [call[2] for call in _closed(harness)] == ["10"]
