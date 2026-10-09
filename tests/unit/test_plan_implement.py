"""Unit tests for the `plan_implement` Workflow against `FakeAgentClient`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from loop import AgentResult
from loop.testing import FakeAgentClient, FakeGit
from workflows import plan_implement

from workflow_harness import PlanImplementHarness, _only_worktree, implement_envelope, plan_envelope


def _sequence(*responses: str) -> Callable[[str, object], str]:
    """A handler answering each call with the next scripted response, in order."""
    remaining = iter(responses)

    def handler(prompt: str, options: object) -> str:
        return next(remaining)

    return handler


def test_the_planning_run_uses_the_planning_model_and_effort_and_the_implementing_run_uses_its_own(
    tmp_path: Path,
) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope()))

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 0
    assert (agent.calls[0][1], agent.calls[0][2]) == (plan_implement.PLAN_MODEL, plan_implement.PLAN_EFFORT)
    assert (agent.calls[1][1], agent.calls[1][2]) == (plan_implement.IMPLEMENT_MODEL, plan_implement.IMPLEMENT_EFFORT)


def test_the_implementing_prompt_contains_the_plan_from_the_planning_run(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(plan="Refactor the widget module."), implement_envelope()))

    harness.run(agent_factory=lambda binding: agent)

    assert "Refactor the widget module." in agent.calls[1][0]


def test_both_runs_are_stateless_fresh_sessions(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope()))

    harness.run(agent_factory=lambda binding: agent)

    assert agent.calls[0][3].session_key is None
    assert agent.calls[1][3].session_key is None


def test_the_workflow_exits_0_when_both_runs_complete(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope()))

    assert harness.run(agent_factory=lambda binding: agent) == 0


def test_the_worktree_is_removed_when_the_implementing_run_leaves_no_changes(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope()))

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 0
    assert harness.git.removed


def test_the_worktree_is_kept_when_the_implementing_run_leaves_changes(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    responses = iter([plan_envelope()])

    def handler(prompt: str, options: object) -> str:
        response = next(responses, None)
        if response is not None:
            return response
        harness.git.commit(_only_worktree(harness.git), "work")
        return implement_envelope()

    agent = FakeAgentClient(handler)

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 0
    assert not harness.git.removed


def test_no_implementing_run_starts_and_the_workflow_exits_1_when_the_plan_is_empty(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(plan="")))

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 1
    assert len(agent.calls) == 1


def test_no_implementing_run_starts_and_the_workflow_exits_1_when_the_planning_run_fails(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(status="failed")))

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 1
    assert len(agent.calls) == 1


def test_the_workflow_exits_1_when_the_implementing_run_fails(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope(status="failed")))

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 1
    assert len(agent.calls) == 2


def test_the_workflow_exits_1_when_the_agent_process_exits_non_zero(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    failing = AgentResult(stdout="", stderr="boom", exit_code=1, response="", success=False)
    agent = FakeAgentClient(lambda prompt, options: failing)

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 1


def test_the_branch_name_and_the_implementing_runs_commits_are_logged(tmp_path: Path, caplog) -> None:
    harness = PlanImplementHarness(tmp_path)
    responses = iter([plan_envelope()])
    made_commit: dict[str, str] = {}

    def handler(prompt: str, options: object) -> str:
        response = next(responses, None)
        if response is not None:
            return response
        made_commit["sha"] = harness.git.commit(_only_worktree(harness.git), "work")
        return implement_envelope()

    agent = FakeAgentClient(handler)

    with caplog.at_level(logging.INFO, logger="workflow.plan_implement"):
        code = harness.run(agent_factory=lambda binding: agent)

    assert code == 0
    assert any(message.startswith("branch loop/run-") for message in caplog.messages)
    assert any(message.startswith("commits:") and made_commit["sha"] in message for message in caplog.messages)


def test_interrupting_a_run_exits_130_and_applies_the_worktree_keeping_rule(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)

    def handler(prompt: str, options: object) -> str:
        raise KeyboardInterrupt()

    agent = FakeAgentClient(handler)

    code = harness.run(agent_factory=lambda binding: agent)

    assert code == 130
    assert harness.git.removed


def test_injected_models_and_efforts_are_used(tmp_path: Path) -> None:
    harness = PlanImplementHarness(tmp_path)
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope()))

    code = harness.run(
        agent_factory=lambda binding: agent,
        plan_model="custom-plan-model",
        plan_effort="low",
        implement_model="custom-implement-model",
        implement_effort="medium",
    )

    assert code == 0
    assert (agent.calls[0][1], agent.calls[0][2]) == ("custom-plan-model", "low")
    assert (agent.calls[1][1], agent.calls[1][2]) == ("custom-implement-model", "medium")


def test_a_caller_supplied_harness_root_and_git_are_used(tmp_path: Path) -> None:
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_git = FakeGit()
    other_git.remote_branches.add("main")
    agent = FakeAgentClient(_sequence(plan_envelope(), implement_envelope()))

    code = plan_implement.main(
        ["do a task"],
        harness_root=other_root,
        git=other_git,
        agent_factory=lambda binding: agent,
    )

    assert code == 0
    assert other_git.removed
