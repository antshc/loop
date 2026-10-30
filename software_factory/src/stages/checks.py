"""The check Stage: run the repository's configured check command on the worktree."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop import AgentWorkspace

from software_factory.events.checks import ChecksPassed
from software_factory.events.feedback import FeedbackBatch
from software_factory.runtime import Evidence, Fail, Hold, Pass, Stage, Verdict, Verify
from software_factory.shared.checks import CheckCommand, CheckRunner


@dataclass(frozen=True)
class ChecksInput:
    worktree: AgentWorkspace
    batch: FeedbackBatch


def as_verify(command: Sequence[str], *, timeout_s: float | None = None, run: CheckRunner | None = None) -> Verify:
    """The check `command` as a Verify callable: it runs in the given worktree, and a failure carries its output."""
    check = CheckCommand(command, run=run, timeout_s=timeout_s)

    def verify(path: Path) -> Fail[Any] | None:
        failure = check(path)
        return None if failure is None else Fail(failure)

    return verify


def run_checks(*, verify: Verify) -> Stage[ChecksInput, Fail[Any] | None, ChecksPassed]:
    """Runs `verify` on the worktree; a failure fails, unless the pull request's CI is red too, which holds so the CI is repaired first."""
    return (
        Stage("run-checks", ChecksInput, produces=ChecksPassed)
        .action(lambda i: verify(i.worktree.path))
        .gate(_checked)
    )


def _checked(i: ChecksInput, failed: Fail[Any] | None, _: Evidence) -> Verdict[ChecksPassed]:
    if failed is None:
        return Pass(ChecksPassed())
    if i.batch.ci_failing:
        return Hold(f"{failed.reason}; CI is failing too")
    return Fail(failed.reason)
