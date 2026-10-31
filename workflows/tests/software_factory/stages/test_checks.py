"""The check Stage and the check command as a Verify callable."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.software_factory.events.checks import ChecksPassed
from workflows.software_factory.events.feedback import FeedbackBatch
from loop import Evidence, Fail, Hold, Pass
from workflows.software_factory.infrastructure.checks import CheckCommand
from loop.shared.process import CommandError
from workflows.software_factory.stages.checks import ChecksInput, as_verify, run_checks
from workflows.tests.software_factory.stages.pr_support import workspace_at

EVIDENCE = Evidence("", "", False)


class FakeRunner:
    """A process runner that answers every command with `outcome`, or raises it when it is an error."""

    def __init__(self, outcome: str | CommandError = "ok") -> None:
        self.outcome = outcome
        self.calls: list[tuple[tuple[str, ...], Path | None, float | None]] = []

    def __call__(self, args, *, cwd: Path | None = None, timeout_s: float | None = None) -> str:
        self.calls.append((tuple(args), cwd, timeout_s))
        if isinstance(self.outcome, CommandError):
            raise self.outcome
        return self.outcome


def batch(*, ci_failing: bool) -> FeedbackBatch:
    return FeedbackBatch("head", ci_failing, ())


def test_the_check_command_runs_in_the_given_worktree_without_a_shell_and_a_success_is_none(tmp_path: Path) -> None:
    """Given a command that succeeds, when the Verify callable runs on a worktree, then it ran that command there with the timeout and reports no failure."""
    runner = FakeRunner()

    failed = as_verify(("python", "-m", "pytest"), timeout_s=30, run=runner)(tmp_path)

    assert failed is None
    assert runner.calls == [(("python", "-m", "pytest"), tmp_path, 30)]


def test_a_failing_check_command_fails_with_its_description_and_the_tail_of_its_output(tmp_path: Path) -> None:
    """Given a command that fails with long output, when the Verify callable runs, then the Fail names the command and keeps only the output's tail."""
    runner = FakeRunner(CommandError("pytest", 1, "start-" + "x" * 5000 + "-end"))

    failed = as_verify(("pytest",), run=runner)(tmp_path)

    assert isinstance(failed, Fail)
    assert failed.reason.startswith("check `pytest` failed: ") and failed.reason.endswith("-end")
    assert "start-" not in failed.reason and len(failed.reason) < 2100


@pytest.mark.parametrize("command", [(), ("",), ("pytest", "")])
def test_a_check_command_needs_a_non_empty_argument_array(command: tuple[str, ...]) -> None:
    """Given an empty command or an empty argument, when the check is built, then it is refused."""
    with pytest.raises(ValueError, match="non-empty argument array"):
        CheckCommand(command)


def test_a_passing_check_produces_checks_passed(tmp_path: Path) -> None:
    """Given a check that passes, when the Stage runs, then it passes with ChecksPassed."""
    stage = run_checks(verify=lambda _: None)
    work = ChecksInput(workspace_at(tmp_path), batch(ci_failing=False))

    verdict = stage.judge(work, stage.executor.run(work), EVIDENCE)

    assert verdict == Pass(ChecksPassed())


def test_a_failing_check_fails_when_ci_is_green_and_holds_when_ci_is_failing_too(tmp_path: Path) -> None:
    """Given a failing check, when the Stage runs against a green and a failing CI, then it fails for the first and holds for the second."""
    stage = run_checks(verify=lambda _: Fail("tests failed"))
    green, red = (ChecksInput(workspace_at(tmp_path), batch(ci_failing=failing)) for failing in (False, True))

    verdicts = [stage.judge(work, stage.executor.run(work), EVIDENCE) for work in (green, red)]

    assert verdicts == [Fail("tests failed"), Hold("tests failed; CI is failing too")]


def test_the_check_runs_on_the_worktree_of_its_scope(tmp_path: Path) -> None:
    """Given a worktree, when the Stage's action runs, then the verify callable receives that worktree's path."""
    seen: list[Path] = []
    stage = run_checks(verify=lambda path: seen.append(path))

    stage.executor.run(ChecksInput(workspace_at(tmp_path), batch(ci_failing=False)))

    assert seen == [tmp_path]
