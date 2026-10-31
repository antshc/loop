"""The plan-and-implement example: one planner run, then one implementer run, on one worktree, through the runtime."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from loop import Role

from workflows.loop import plan_implement
from workflows.loop.plan_implement import IMPLEMENT_INSTRUCTIONS, PLAN_INSTRUCTIONS

from .workflow_harness import (
    FakeGitService,
    FakeRunner,
    crash,
    implement_envelope,
    new_agent_for,
    plan_envelope,
)

LOGGER = "workflow.plan_implement"


class FakeGit:
    """Answers the Workflow's Git reads: HEAD before implementing and the commits since it."""

    def __init__(self, commits: str = "") -> None:
        self.commits = commits
        self.reads: list[tuple[Path, list[str]]] = []

    def __call__(self, path: Path, args: list[str]) -> str:
        self.reads.append((path, args))
        return "base" if args[0] == "rev-parse" else self.commits


class Rig:
    """A fake agent CLI, a fake Git service and a fake Git reader around `plan_implement.main`."""

    def __init__(self, tmp_path: Path, *answers: object, commits: str = "") -> None:
        self.answers = list(answers)
        self.runner = FakeRunner(self.answer)
        self.git_service = FakeGitService(tmp_path / "worktree")
        self.git = FakeGit(commits)
        self.cwd = tmp_path

    def answer(self, prompt: str) -> str:
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(prompt)
        return str(answer)

    def main(self, *argv: str, **overrides: object) -> int:
        return plan_implement.main(
            list(argv) or ["add a feature"],
            read_git=self.git,
            new_agent=new_agent_for(self.runner, self.git_service, self.cwd),
            **overrides,
        )


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_help_shows_a_single_positional_task(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the entry point, when --help is asked, then it shows one positional task and exits 0."""
    with pytest.raises(SystemExit) as exited:
        plan_implement.main(["--help"])

    assert exited.value.code == 0
    assert re.search(r"usage: plan_implement \[-h\] task", capsys.readouterr().out)


def test_two_successful_runs_feed_the_plan_to_the_implementer_on_one_worktree(
    tmp_path: Path,
) -> None:
    """Given a planner and an implementer that both succeed, when the Workflow runs, then it exits 0 and the implementer starts from the plan on the same worktree."""
    rig = Rig(
        tmp_path, plan_envelope(plan="step one"), implement_envelope(), commits="c1\nc2"
    )

    assert rig.main("add a feature") == 0

    assert rig.runner.prompts[0] == f"add a feature\n\n{PLAN_INSTRUCTIONS}"
    assert rig.runner.prompts[1] == f"step one\n\n{IMPLEMENT_INSTRUCTIONS}"
    assert len(rig.git_service.opened) == 1
    assert {path for path, _ in rig.git.reads} == {tmp_path / "worktree"}


def test_a_plan_without_a_completed_tag_stops_before_implementing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Given a planner output with no completed tag, when the Workflow runs, then it exits 1 and the implementer never runs."""
    rig = Rig(tmp_path, "just some text")

    with caplog.at_level(logging.INFO, LOGGER):
        assert rig.main() == 1

    assert len(rig.runner.prompts) == 1
    assert "no <completed> tag in agent output" in caplog.text
    assert "planning run failed: no usable plan" in caplog.text


def test_a_failed_plan_stops_before_implementing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Given a planner that reports a failure, when the Workflow runs, then it exits 1 and the implementer never runs."""
    rig = Rig(tmp_path, plan_envelope("failed", reason="blocked"))

    with caplog.at_level(logging.INFO, LOGGER):
        assert rig.main() == 1

    assert len(rig.runner.prompts) == 1
    assert "agent reported: Failure: blocked" in caplog.text


def test_a_failed_implementation_still_logs_the_commits_since_the_recorded_head(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Given an implementer that reports a failure after committing, when the Workflow runs, then it exits 1 and logs the commits."""
    rig = Rig(tmp_path, plan_envelope(), implement_envelope("failed"), commits="c1")

    with caplog.at_level(logging.INFO, LOGGER):
        assert rig.main() == 1

    assert ["log", "--reverse", "--format=%H", "base..HEAD"] in [
        args for _, args in rig.git.reads
    ]
    assert "commits: c1" in caplog.text
    assert "implementing run failed" in caplog.text


def test_an_implementation_without_commits_succeeds_and_reports_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Given an implementer that succeeds without committing, when the Workflow runs, then it exits 0 and logs no commits."""
    rig = Rig(tmp_path, plan_envelope(), implement_envelope())

    with caplog.at_level(logging.INFO, LOGGER):
        assert rig.main() == 0

    assert "commits: none" in caplog.text


@pytest.mark.parametrize("failing", ["planner", "implementer"])
def test_a_failing_agent_command_is_logged_with_its_stderr_and_exits_1(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failing: str,
) -> None:
    """Given an agent command that exits non-zero, when the Workflow runs, then the error and its stderr are logged and it exits 1."""
    answers = [crash()] if failing == "planner" else [plan_envelope(), crash()]
    rig = Rig(tmp_path, *answers)

    with caplog.at_level(logging.INFO, LOGGER):
        assert rig.main() == 1

    assert "agent command failed" in caplog.text and "stderr: crashed" in caplog.text


@pytest.mark.parametrize(
    "answers",
    [[KeyboardInterrupt()], [plan_envelope(), KeyboardInterrupt()]],
    ids=["planning", "implementing"],
)
def test_an_interrupt_during_either_run_exits_130(
    tmp_path: Path, answers: list[object]
) -> None:
    """Given a keyboard interrupt during a run, when the Workflow runs, then it exits 130."""
    assert Rig(tmp_path, *answers).main() == 130


def test_the_defaults_pick_the_planner_and_implementer_models_on_a_fresh_branch_off_origin_main(
    tmp_path: Path,
) -> None:
    """Given the default settings, when the Workflow runs, then the planner and implementer use their default profiles on a loop/run branch off origin/main."""
    rig = Rig(tmp_path, plan_envelope(), implement_envelope())

    rig.main()

    planner, implementer = rig.runner.profiles
    assert (
        planner.cli.name,
        planner.model,
        planner.reasoning_effort,
        planner.name,
    ) == ("copilot", "claude-opus-5.5", "max", Role.PLANNER)
    assert (
        implementer.cli.name,
        implementer.model,
        implementer.reasoning_effort,
        implementer.name,
    ) == (
        "copilot",
        "claude-sonnet-5.5",
        "high",
        Role.DEVELOPER,
    )
    strategy = rig.git_service.opened[0].strategy
    assert (
        re.fullmatch(r"loop/run-[0-9a-f]{8}", strategy.branch)
        and strategy.base_branch == "origin/main"
    )
