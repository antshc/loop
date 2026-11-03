"""The check Stage: run the repository's configured check command on the worktree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loop import AgentWorkspace, Evidence, Fail, Hold, Pass, Stage, Verdict, Verify
from loop.software_factory.events.checks import ChecksPassed

from software_factory.src.pull_requests.events.feedback import FeedbackBatch


@dataclass(frozen=True)
class ChecksInput:
    worktree: AgentWorkspace
    batch: FeedbackBatch


def run_checks(*, verify: Verify) -> Stage[ChecksInput, Fail[Any] | None, ChecksPassed]:
    """Runs `verify` on the worktree; a failure fails, unless the pull request's CI is red too, which holds so the CI is repaired first."""
    return (
        Stage("run-checks", ChecksInput, produces=ChecksPassed)
        .action(lambda i: verify(i.worktree.path))
        .gate(_checked)
    )


def _checked(
    i: ChecksInput, failed: Fail[Any] | None, _: Evidence
) -> Verdict[ChecksPassed]:
    if failed is None:
        return Pass(ChecksPassed())
    if i.batch.ci_failing:
        return Hold(f"{failed.reason}; CI is failing too")
    return Fail(failed.reason)
