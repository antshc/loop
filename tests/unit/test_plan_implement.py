from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from workflow_harness import (
    FakeGitService,
    FakeRunner,
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
        self.commits = ["base0000"]
        self.git_reads: list[tuple[Path, list[str]]] = []

    def read_git(self, path: Path, args: list[str]) -> str:
        self.git_reads.append((path, args))
        if args == ["rev-parse", "HEAD"]:
            return self.commits[-1]
        assert args[:3] == ["log", "--reverse", "--format=%H"]
        base = args[3].removesuffix("..HEAD")
        return "\n".join(self.commits[self.commits.index(base) + 1 :])

    def run(self, *responses: str | Callable[[str], str], task: str = "add a widget") -> int:
        remaining = iter(responses)

        def handler(prompt: str) -> str:
            response = next(remaining)
            return response(prompt) if callable(response) else response

        self.runner.handler = handler
        return plan_implement.main(
            [task],
            read_git=self.read_git,
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
        harness.commits.extend(["first000", "second00"])
        return implement_envelope()

    with caplog.at_level(logging.INFO, logger="workflow.plan_implement"):
        harness.run(plan_envelope(), implement)

    branch = harness.git_service.opened[0].strategy.branch
    assert f"branch {branch}" in caplog.text
    assert "commits: first000, second00" in caplog.text
    assert harness.git_reads == [
        (harness.git_service.worktree, ["rev-parse", "HEAD"]),
        (harness.git_service.worktree, ["log", "--reverse", "--format=%H", "base0000..HEAD"]),
    ]


def test_an_interrupt_exits_130_and_still_closes_the_worktree(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    def interrupt(prompt: str) -> str:
        raise KeyboardInterrupt

    code = harness.run(interrupt)

    assert code == 130 and harness.git_service.closed == 1


@pytest.mark.parametrize(
    ("response", "body"),
    [
        ("plan\n<completed>Success</completed>", "plan"),
        ("  line one\nline two  \n<completed> success </completed>\nnoise", "line one\nline two"),
        ("<completed>SUCCESS</completed>", ""),
    ],
)
def test_response_parsing_returns_only_the_body(response: str, body: str) -> None:
    assert plan_implement._response_body(response) == body


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ("no response", "no <completed> tag"),
        ("<completed>Failure: blocked</completed>", "Failure: blocked"),
        ("<completed>unknown</completed>", "unknown"),
    ],
)
def test_response_parsing_rejects_missing_and_unsuccessful_tags(response: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        plan_implement._response_body(response)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (plan_envelope(status="failed", reason="blocked"), "blocked"),
        ("no response", "no <completed> tag"),
        (crash(), "crashed"),
    ],
)
def test_planning_failures_log_details_without_reading_git(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, response: str | Callable[[str], str], reason: str,
) -> None:
    harness = Harness(tmp_path)

    assert harness.run(response) == 1
    assert reason in caplog.text
    assert harness.git_reads == []
    assert harness.git_service.closed == 1


def test_task_and_plan_are_passed_as_literal_text(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    task = "fix {widget}\n$(do not execute)"
    plan = "Use {{literal}} and ${unchanged}.\nKeep both lines."

    assert harness.run(plan_envelope(plan=plan), implement_envelope(), task=task) == 0
    assert harness.runner.prompts == [
        f"{task}\n\n{plan_implement.PLAN_INSTRUCTIONS}",
        f"{plan}\n\n{plan_implement.IMPLEMENT_INSTRUCTIONS}",
    ]


def test_failed_implementation_still_reports_commits_and_closes_worktree(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness(tmp_path)

    def implement(prompt: str) -> str:
        harness.commits.append("partial0")
        return implement_envelope(status="failed", reason="tests failed")

    with caplog.at_level(logging.INFO, logger="workflow.plan_implement"):
        assert harness.run(plan_envelope(), implement) == 1

    assert "commits: partial0" in caplog.text
    assert "tests failed" in caplog.text
    assert harness.git_service.closed == 1


def test_git_output_uses_the_worktree_and_checked_noninteractive_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "--no-pager", "rev-parse", "HEAD"]
        assert cwd == tmp_path
        assert check and capture_output and text
        return subprocess.CompletedProcess(command, 0, stdout="base0000\n")

    monkeypatch.setattr(subprocess, "run", run)
    assert plan_implement._git_output(tmp_path, ["rev-parse", "HEAD"]) == "base0000"


def test_git_failure_propagates_and_closes_worktree(tmp_path: Path) -> None:
    runner = FakeRunner(lambda prompt: plan_envelope())
    git_service = FakeGitService(tmp_path / "worktree")

    def read_git(path: Path, args: list[str]) -> str:
        raise subprocess.CalledProcessError(128, ["git", *args], stderr="bad revision")

    with pytest.raises(subprocess.CalledProcessError):
        plan_implement.main(
            ["task"], read_git=read_git, new_agent=new_agent_for(runner, git_service, tmp_path),
        )

    assert len(runner.prompts) == 1
    assert git_service.closed == 1
