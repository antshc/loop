"""Integration tests: `dev.main` publishes a Spec's local feature branch when it is ahead of its remote.

Only Git, GitHub, the Copilot CLI process, and the execution store are stand-ins
(docs/concepts/str-loop-library-workflow-architecture.md, docs/concepts/dom-autonomous-spec-delivery.md).
"""

from __future__ import annotations

from pathlib import Path

from loop import InMemorySessionStore, NoSandbox, copilot
from loop.testing import FakeCopilotCli

from workflow_harness import DevHarness, RecordingExecutor, _only_worktree, copilot_event_frames

_IDENTIFIER = "Checkout|10"
_FEATURE_BRANCH = "add-login-page"


def _run_with_real_agent(harness: DevHarness, fake_cli: FakeCopilotCli) -> tuple[int, RecordingExecutor]:
    recorder = RecordingExecutor(fake_cli)
    code = harness.run(
        agent_factory=copilot(InMemorySessionStore()),
        sandbox_factory=lambda workspace, cancel: NoSandbox(workspace, executor=recorder, cancel=cancel),
    )
    return code, recorder


def _completed_frames(commit: str) -> list[str]:
    return copilot_event_frames(_IDENTIFIER, "completed", {"commit": commit, "summary": "done", "verification": "ran tests"})


def _spy_on_worktree_creation(harness: DevHarness) -> list[bool]:
    """Records, at each worktree creation, whether a push had already happened."""
    created_after_push: list[bool] = []
    original = harness.git.worktree_service.create

    def spy(*args, **kwargs):
        created_after_push.append(bool(harness.git.pushed))
        return original(*args, **kwargs)

    harness.git.worktree_service.create = spy
    return created_after_push


def test_a_branch_ahead_from_an_interrupted_run_is_pushed_before_the_worktree_and_its_earlier_commit_survives(
    tmp_path: Path,
) -> None:
    """Given a local feature branch already carries an earlier, interrupted run's commit for a Ticket closed
    before this run started, when the Spec runs and delivers its remaining Ticket, then the branch is pushed
    before the worktree is created, the draft pull request is ensured, and the earlier commit is still on the
    branch after the run."""
    harness = DevHarness(tmp_path)
    harness.git.branches[_FEATURE_BRANCH] = ["0000000000000000000000000000000000000009"]
    harness.git.subjects["0000000000000000000000000000000000000009"] = "ccode(Checkout|9): earlier"
    created_after_push = _spy_on_worktree_creation(harness)

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return _completed_frames(commit)

    code, recorder = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 0
    assert recorder.lines and not recorder.terminated
    assert created_after_push == [True]
    assert harness.git.pushed[0] == (harness.harness_root, _FEATURE_BRANCH)
    assert harness.git.branches[_FEATURE_BRANCH][0] == "0000000000000000000000000000000000000009"
    assert any(call[:2] == ("pr", "create") for call in harness.gh.calls)
    closes = [call for call in harness.gh.calls if call[:2] == ("issue", "close")]
    assert [call[2] for call in closes] == ["10"]


def test_a_cancelled_run_that_left_an_unpublished_ticket_commit_checked_out_at_the_worktree_is_kept_and_published(
    tmp_path: Path,
) -> None:
    """Given a previous run was cancelled after an unpublished Ticket commit (the feature branch has a local
    `ccode(...)` commit not on origin and is checked out at the intended worktree path), when the Spec reruns,
    then the commit is kept, the branch is pushed, and the draft pull request is ensured."""
    harness = DevHarness(tmp_path)
    target = harness.harness_root / "workspace" / f"{harness.harness_root.name}.worktrees" / _FEATURE_BRANCH
    harness.git.worktrees[target] = _FEATURE_BRANCH
    harness.git.branches[_FEATURE_BRANCH] = ["0000000000000000000000000000000000000009"]
    harness.git.subjects["0000000000000000000000000000000000000009"] = "ccode(Checkout|9): earlier"

    def handler(prompt: str) -> list[str]:
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({_IDENTIFIER}): work")
        return _completed_frames(commit)

    code, recorder = _run_with_real_agent(harness, FakeCopilotCli(handler))

    assert code == 0
    assert recorder.lines and not recorder.terminated
    assert harness.git.branches[_FEATURE_BRANCH][0] == "0000000000000000000000000000000000000009"
    assert harness.git.pushed and harness.git.pushed[0] == (harness.harness_root, _FEATURE_BRANCH)
    assert any(call[:2] == ("pr", "create") for call in harness.gh.calls)


def test_a_spec_with_no_actionable_ticket_and_a_branch_ahead_is_pushed_with_its_draft_pull_request(
    tmp_path: Path,
) -> None:
    """Given a Spec has no actionable Tickets and its local feature branch is ahead of its remote, when the
    Spec is processed, then the branch is pushed and the draft pull request is ensured."""
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.git.branches[_FEATURE_BRANCH] = ["0000000000000000000000000000000000000001"]

    code = harness.run()

    assert code == 0
    assert harness.git.pushed == [(harness.harness_root, _FEATURE_BRANCH)]
    assert any(call[:2] == ("pr", "create") for call in harness.gh.calls)


def test_a_spec_with_nothing_ahead_of_its_remote_pushes_nothing_when_its_delivery_ends(tmp_path: Path) -> None:
    """Given a Spec has no local commits ahead of its remote, when its delivery ends, then nothing is
    pushed and no pull request is created."""
    harness = DevHarness(tmp_path, tickets={1: []})

    code = harness.run()

    assert code == 0
    assert harness.git.pushed == []
    assert all(call[:2] != ("pr", "create") for call in harness.gh.calls)
