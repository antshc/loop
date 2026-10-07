"""Integration test: `dev.main` through the real Copilot agent client, parser, prompt preprocessor, and host sandbox.

Only Git, GitHub, the Copilot CLI process, and the execution store are stand-ins
(docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

import re
from pathlib import Path

from loop import InMemorySessionStore, NoSandbox, copilot
from loop.testing import FakeCopilotCli

from workflow_harness import DevHarness, RecordingExecutor, _issue, _only_worktree, copilot_event_frames

_TASK_ID = re.compile(r"Task id: `([^`]+)`")


def _scripted_agent(harness: DevHarness) -> FakeCopilotCli:
    """Commits once via the git stand-in, then replies `completed` for whichever task id the prompt carries."""

    def handler(prompt: str) -> list[str]:
        identifier = _TASK_ID.search(prompt)[1]
        commit = harness.git.commit(_only_worktree(harness.git), f"ccode({identifier}): work")
        return copilot_event_frames(identifier, "completed", {"commit": commit, "summary": "done", "verification": "ran tests"})

    return FakeCopilotCli(handler)


def _prompt_of(call: tuple[str, ...]) -> str:
    return call[call.index("-p") + 1]


def _run_with_real_agent(harness: DevHarness, fake_cli: FakeCopilotCli) -> tuple[int, RecordingExecutor]:
    recorder = RecordingExecutor(fake_cli)
    code = harness.run(
        agent_factory=copilot(InMemorySessionStore()),
        sandbox_factory=lambda workspace, cancel: NoSandbox(workspace, executor=recorder, cancel=cancel),
    )
    return code, recorder


def test_two_actionable_tickets_each_get_a_fresh_session_less_run_and_one_publish(tmp_path: Path) -> None:
    """Given a Spec with two actionable Tickets, when each run commits once and responds completed, then two
    fresh runs happen in order, each Ticket closes once with its own evidence, and the branch publishes with
    one draft pull request while the Spec stays open."""
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})
    fake_cli = _scripted_agent(harness)

    code, recorder = _run_with_real_agent(harness, fake_cli)

    assert code == 0
    assert recorder.lines and not recorder.terminated
    assert len(fake_cli.calls) == 2
    for call in fake_cli.calls:
        assert not any(arg == "--name" or arg.startswith("--resume=") for arg in call)
    closes = [call for call in harness.gh.calls if call[:2] == ("issue", "close")]
    assert [call[2] for call in closes] == ["10", "11"]
    assert all("done" in call[-1] and "ran tests" in call[-1] for call in closes)
    assert len(harness.git.pushed) == 1
    assert len([call for call in harness.gh.calls if call[:2] == ("pr", "create")]) == 1
    comments = [call for call in harness.gh.calls if call[:2] == ("issue", "comment")]
    assert any(call[2] == "1" and "draft pull request" in call[-1] for call in comments)
    assert all(call[2] != "1" for call in closes)


def test_each_tickets_prompt_carries_only_that_tickets_content(tmp_path: Path) -> None:
    """Given two actionable Tickets, when their prompts reach the Copilot CLI stand-in, then each carries only
    that Ticket's content and task id, not the Spec body or the other Ticket's content."""
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})
    fake_cli = _scripted_agent(harness)

    code, _ = _run_with_real_agent(harness, fake_cli)

    assert code == 0
    prompts = [_prompt_of(call) for call in fake_cli.calls]
    assert "Checkout|10" in prompts[0] and "Body of #10" in prompts[0]
    assert "Body of #1" not in prompts[0].replace("Body of #10", "") and "Add login tests" not in prompts[0]
    assert "Checkout|11" in prompts[1] and "Body of #11" in prompts[1]
    assert "Body of #1" not in prompts[1].replace("Body of #11", "") and "Add login form" not in prompts[1]


def test_prompt_lists_only_this_initiatives_commits_when_the_branch_holds_two_initiatives(tmp_path: Path) -> None:
    """Given the feature branch already holds task commits from two Initiatives, when this Initiative's Ticket
    is delivered, then its prompt lists only this Initiative's commits since the base branch, and its split
    response is reassembled and treated as a success."""
    harness = DevHarness(tmp_path)
    harness.git.branches["add-login-page"] = ["c1", "c2"]
    harness.git.subjects.update({"c1": "ccode(Checkout|9): earlier work", "c2": "ccode(Other|3): other initiative"})
    fake_cli = _scripted_agent(harness)

    code, _ = _run_with_real_agent(harness, fake_cli)

    assert code == 0
    prompt = _prompt_of(fake_cli.calls[0])
    assert "ccode(Checkout|9): earlier work" in prompt
    assert "other initiative" not in prompt
