from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from workflow_harness import (
    FakeGitService,
    FakeRunner,
    FakeWorkflowGit,
    crash,
    implement_envelope,
    new_agent_for,
    plan_envelope,
)
from workflows import plan_implement


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.runner = FakeRunner()
        self.git_service = FakeGitService(tmp_path / "worktree")
        self.git = FakeWorkflowGit()

    def run(self, *responses: str | Callable[[str], str], task: str = "add a widget") -> int:
        remaining = iter(responses)

        def handler(prompt: str) -> str:
            response = next(remaining)
            return response(prompt) if callable(response) else response

        self.runner.handler = handler
        return plan_implement.main(
            [task],
            git=self.git,  # type: ignore[arg-type]
            new_agent=new_agent_for(self.runner, self.git_service, Path.cwd()),
        )


def test_each_run_uses_its_own_model_and_effort(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    code = harness.run(plan_envelope(), implement_envelope())

    assert code == 0
    planner, implementer = harness.runner.profiles
    assert (planner.model, planner.reasoning_effort) == (plan_implement.PLAN_MODEL, plan_implement.PLAN_EFFORT)
    assert (implementer.model, implementer.reasoning_effort) == (
        plan_implement.IMPLEMENT_MODEL,
        plan_implement.IMPLEMENT_EFFORT,
    )


def test_the_planning_prompt_carries_the_task_and_the_implementing_prompt_the_plan(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    harness.run(plan_envelope(plan="Refactor the widget module."), implement_envelope())

    assert harness.runner.prompts[0].startswith("add a widget")
    assert "Refactor the widget module." in harness.runner.prompts[1]


def test_both_runs_share_one_worktree_on_a_fresh_branch_off_origin_main(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    harness.run(plan_envelope(), implement_envelope())

    assert len(harness.git_service.opened) == 1 and harness.git_service.closed == 1
    strategy = harness.git_service.opened[0].strategy
    assert strategy.branch.startswith("loop/run-") and strategy.base_branch == "origin/main"


def test_noise_around_the_response_is_ignored(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    code = harness.run(f"thinking\n{plan_envelope()}\ndone", f"{implement_envelope()}\n")

    assert code == 0


@pytest.mark.parametrize("plan", [plan_envelope(plan=""), plan_envelope(status="failed"), "no response", crash()])
def test_no_implementing_run_starts_and_the_exit_code_is_1_when_planning_yields_no_plan(
    tmp_path: Path, plan: str | Callable[[str], str]
) -> None:
    harness = Harness(tmp_path)

    code = harness.run(plan)

    assert code == 1 and len(harness.runner.prompts) == 1


@pytest.mark.parametrize("implement", [implement_envelope(status="failed"), "no response", crash()])
def test_the_exit_code_is_1_when_the_implementing_run_fails(
    tmp_path: Path, implement: str | Callable[[str], str]
) -> None:
    harness = Harness(tmp_path)

    code = harness.run(plan_envelope(), implement)

    assert code == 1 and len(harness.runner.prompts) == 2


def test_the_branch_name_and_the_implementing_runs_commits_are_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    harness = Harness(tmp_path)

    def implement(prompt: str) -> str:
        harness.git.commit("work")
        return implement_envelope()

    with caplog.at_level(logging.INFO, logger="workflow.plan_implement"):
        harness.run(plan_envelope(), implement)

    branch = harness.git_service.opened[0].strategy.branch
    assert f"branch {branch}" in caplog.text and harness.git.commits[-1].sha in caplog.text


def test_an_interrupt_exits_130_and_still_closes_the_worktree(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    def interrupt(prompt: str) -> str:
        raise KeyboardInterrupt

    code = harness.run(interrupt)

    assert code == 130 and harness.git_service.closed == 1
