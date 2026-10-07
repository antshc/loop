"""Integration tests: `dev.main` validates a `completed` response against Git, counts failures, retries,
escalates to a human, and still attempts a Spec whose Ticket failed in an earlier run.

Only Git, GitHub, the Copilot CLI process, and the execution store are stand-ins
(docs/concepts/str-loop-library-workflow-architecture.md, docs/concepts/dom-autonomous-spec-delivery.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from loop import InMemorySessionStore, NoSandbox, copilot
from loop.testing import FakeCopilotCli

from workflow_harness import DevHarness, RecordingExecutor, _issue, _only_worktree, copilot_event_frames

_IDENTIFIER = "Checkout|10"
_TASK_ID = re.compile(r"Task id: `([^`]+)`")


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


def _reason_comments(harness: DevHarness) -> list[str]:
    return [call[-1] for call in harness.gh.calls if call[:2] == ("issue", "comment")]


def _completed_frames(identifier: str, commit: str) -> list[str]:
    return copilot_event_frames(identifier, "completed", {"commit": commit, "summary": "done", "verification": "ran tests"})


def _head_unchanged(harness: DevHarness):
    def handler(prompt: str) -> list[str]:
        return _completed_frames(_IDENTIFIER, "deadbeef")

    return handler


def _non_matching_subject(harness: DevHarness):
    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), "feat: wrong subject")
        return _completed_frames(_IDENTIFIER, commit)

    return handler


def _dirty_tree(harness: DevHarness):
    def handler(prompt: str) -> list[str]:
        worktree = _only_worktree(harness.git)
        commit = harness.git.commit(worktree, f"ccode({_IDENTIFIER}): work")
        harness.git.dirty_worktrees.add(worktree)
        return _completed_frames(_IDENTIFIER, commit)

    return handler


def _wrong_reported_commit(harness: DevHarness):
    def handler(prompt: str) -> list[str]:
        harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return _completed_frames(_IDENTIFIER, "deadbeef")

    return handler


def _other_tickets_identifier(harness: DevHarness):
    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return _completed_frames("Checkout|11", commit)

    return handler


@pytest.mark.parametrize(
    ("handler_for", "reason"),
    [
        (_head_unchanged, "HEAD did not change"),
        (_non_matching_subject, "does not start with"),
        (_dirty_tree, "uncommitted"),
        (_wrong_reported_commit, "result.commit"),
        (_other_tickets_identifier, "does not match task"),
    ],
)
def test_a_completed_response_failing_git_validation_resets_the_worktree_and_escalates_after_a_retry(
    tmp_path: Path, handler_for, reason: str
) -> None:
    """Given every run of a Ticket reports `completed` but fails Git validation the same way, when the
    Workflow retries once with a fresh run and fails again, then the run fails, the worktree is reset to the
    HEAD recorded before the run, and the Ticket and Spec are labelled hitl with the reason commented on
    both."""
    harness = DevHarness(tmp_path)

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler_for(harness)))

    assert code == 1
    assert harness.git.branches["add-login-page"] == []
    assert _closed(harness) == []
    assert _hitl_labelled(harness) == {"1", "10"}
    assert any(reason in comment for comment in _reason_comments(harness))
    assert harness.git.pushed == [] and all(call[:2] != ("pr", "create") for call in harness.gh.calls)


def test_a_first_git_validation_failure_is_retried_with_a_fresh_run_and_the_retrys_success_closes_and_clears_the_count(
    tmp_path: Path,
) -> None:
    """Given a Ticket's first run reports `completed` with no commit, when the Workflow retries with a fresh
    run and the retry commits and reports `completed`, then the Ticket closes and its failure count is
    cleared."""
    harness = DevHarness(tmp_path)
    attempts: list[int] = []

    def handler(prompt: str) -> list[str]:
        attempts.append(1)
        if len(attempts) == 1:
            return _completed_frames(_IDENTIFIER, "deadbeef")
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return _completed_frames(_IDENTIFIER, commit)

    fake_cli = FakeCopilotCli(handler)
    code, _ = _run_with_real_agent(harness, fake_cli)

    assert code == 0
    assert len(fake_cli.calls) == 2
    assert [call[2] for call in _closed(harness)] == ["10"]
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/10") == 0
    assert _hitl_labelled(harness) == set()


def test_a_failed_status_reported_after_committing_discards_the_commit_and_uses_the_agents_reason(
    tmp_path: Path,
) -> None:
    """Given the agent commits to the worktree then reports status `failed` with its own reason on every
    attempt, when the Workflow retries once and still fails, then the commit is discarded on each attempt,
    the worktree is reset, and the agent's own reason is the comment on the Ticket and the Spec."""
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> list[str]:
        harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): half-done work")
        return copilot_event_frames(_IDENTIFIER, "failed", {"reason": "the acceptance tests do not pass"})

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 1
    assert harness.git.branches["add-login-page"] == []
    assert harness.git.pushed == []
    assert _closed(harness) == []
    assert all("the acceptance tests do not pass" in comment for comment in _reason_comments(harness))


def test_a_second_tickets_escalation_stops_delivery_but_still_publishes_the_first_tickets_close(
    tmp_path: Path,
) -> None:
    """Given two actionable Tickets where the first closes normally and the second fails Git validation on
    every attempt, when the second Ticket is escalated to hitl, then no further Ticket is delivered, yet the
    first Ticket's commit is still pushed and its draft pull request is ensured."""
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})

    def handler(prompt: str) -> list[str]:
        identifier = _TASK_ID.search(prompt)[1]
        if identifier == "Checkout|10":
            commit = harness.git.commit(_only_worktree(harness.git), f"ccode({identifier}): work")
            return _completed_frames(identifier, commit)
        return _completed_frames(identifier, "deadbeef")

    code, _ = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 1
    assert [call[2] for call in _closed(harness)] == ["10"]
    assert _hitl_labelled(harness) == {"1", "11"}
    assert len(harness.git.pushed) == 1
    assert any(call[:2] == ("pr", "create") for call in harness.gh.calls)
    spec_comments = [call[-1] for call in harness.gh.calls if call[:2] == ("issue", "comment") and call[2] == "1"]
    assert all("all Tickets delivered" not in comment for comment in spec_comments)


def test_a_spec_not_labelled_hitl_whose_ticket_failed_in_earlier_runs_is_attempted_with_no_spec_level_cap(
    tmp_path: Path,
) -> None:
    """Given the execution store already carries several persisted failures for a Ticket on a Spec that is
    not labelled hitl, when the Workflow attempts the Spec again and the run commits and reports `completed`,
    then the Ticket closes and its failure count is cleared, proving there is no Spec-level attempt cap."""
    harness = DevHarness(tmp_path)
    for _ in range(5):
        harness.store.record_failure(
            "https://github.com/owner/repo/issues/10", owner="owner", repo="repo", task_id="10", title="x", items=[10]
        )
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/10") == 5

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return _completed_frames(_IDENTIFIER, commit)

    fake_cli = FakeCopilotCli(handler)
    code, _ = _run_with_real_agent(harness, fake_cli)

    assert code == 0
    assert len(fake_cli.calls) == 1
    assert [call[2] for call in _closed(harness)] == ["10"]
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/10") == 0
