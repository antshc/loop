"""Example Workflow: plan a task with a strong model, implement it with a cheaper one, on one worktree.

One worktree, two stateless runs (an `AgentProfile` per step; the Shared Worktree Agent Run variant,
docs/concepts/str-agent-run.md). No push, pull request, or Ticket access.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loop import (
    Agent,
    AgentBuilder,
    AgentProfile,
    AgentRequest,
    BranchStrategy,
    ContentDecodeError,
    GitOptions,
    AgentWorkspace,
    copilot,
)
from workflows.platforms.git import GitService

logger = logging.getLogger("workflow.plan_implement")

PLAN_MODEL = "claude-opus-5.5"
PLAN_EFFORT = "max"
IMPLEMENT_MODEL = "claude-sonnet-5.5"
IMPLEMENT_EFFORT = "high"

PLAN_INSTRUCTIONS = """You are planning a coding task on this repository. Do not write or change any code.

Write a concise, actionable implementation plan for another agent to follow.

End your final message with exactly one of these tags, with nothing after it:
<completed>Success</completed>
<completed>Failure: <why></completed>
"""

IMPLEMENT_INSTRUCTIONS = """You are implementing a coding task on this repository, following the plan above.

Implement the plan. Commit your work with git. Do not push.

End your final message with exactly one of these tags, with nothing after it:
<completed>Success</completed>
<completed>Failure: <why></completed>
"""

_COMPLETED_TAG = re.compile(r"<completed>(.*?)</completed>", re.DOTALL)


@dataclass(frozen=True)
class StepResult:
    """One step's reply: whether its `<completed>` tag signals success, the text written before it, and why not."""

    completed: bool
    body: str
    reason: str | None = None


class StepResponse:
    """Decodes a step's `<completed>Success</completed>` / `<completed>Failure: <why></completed>` reply."""

    def decode(self, text: str) -> StepResult:
        match = _COMPLETED_TAG.search(text)
        if match is None:
            raise ContentDecodeError("no <completed> tag in agent output", text)
        body = text[: match.start()].strip()
        tag = match.group(1).strip()
        if tag.lower() == "success":
            return StepResult(True, body)
        reason = tag.split(":", 1)[1].strip() if ":" in tag else tag
        return StepResult(False, body, reason)


STEP = StepResponse()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="plan_implement")
    parser.add_argument("task")
    return parser.parse_args(argv)


def _run_step(worktree: AgentWorkspace, profile: AgentProfile, prompt: str) -> str | None:
    """Runs one fresh, stateless step; the text it wrote before its `<completed>` tag, or None when it failed."""
    try:
        output = worktree.agent(profile).run(AgentRequest(prompt)).output
        result = STEP.decode(output)
    except (subprocess.CalledProcessError, ContentDecodeError):
        return None
    return result.body if result.completed else None


def _run(worktree: AgentWorkspace, git: GitService, task: str, planner: AgentProfile, implementer: AgentProfile) -> int:
    # The agent-written plan and the task are embedded as text and sent verbatim; they are never preprocessed.
    plan = _run_step(worktree, planner, f"{task}\n\n{PLAN_INSTRUCTIONS}")
    if not plan:
        logger.error("planning run failed")
        return 1

    head_before = git.head(worktree.path)
    implement_result = _run_step(worktree, implementer, f"{plan}\n\n{IMPLEMENT_INSTRUCTIONS}")
    commits = git.commits_since(worktree.path, head_before.sha)
    logger.info("commits: %s", ", ".join(commit.sha for commit in commits) or "none")
    if implement_result is None:
        logger.error("implementing run failed")
        return 1
    return 0


def _git_options(branch: str) -> GitOptions:
    return GitOptions(
        root_path=Path("workspace") / f"{Path.cwd().name}.worktrees",
        strategy=BranchStrategy(branch, "origin/main"),
    )


def main(
    argv: list[str] | None = None,
    *,
    plan_model: str = PLAN_MODEL,
    plan_effort: str = PLAN_EFFORT,
    implement_model: str = IMPLEMENT_MODEL,
    implement_effort: str = IMPLEMENT_EFFORT,
    git: GitService | None = None,
    new_agent: Callable[[], AgentBuilder] = Agent,
) -> int:
    """Plans `argv`'s task with `plan_model`, then implements it with `implement_model`; returns the exit code."""
    args = _parse_args(argv)
    git = git or GitService()
    planner = AgentProfile(copilot, plan_model, plan_effort)
    implementer = AgentProfile(copilot, implement_model, implement_effort)
    branch = f"loop/run-{uuid.uuid4().hex[:8]}"
    logger.info("branch %s", branch)

    with new_agent().with_git(_git_options(branch)).open() as worktree:
        try:
            return _run(worktree, git, args.task, planner, implementer)
        except KeyboardInterrupt:
            logger.warning("interrupted")
            return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
