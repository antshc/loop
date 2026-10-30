"""Self-contained Workflow: plan, implement, and report commits on one worktree without pushing."""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from loop import (
    Agent,
    AgentBuilder,
    AgentProfile,
    AgentRequest,
    AgentWorkspace,
    BranchStrategy,
    GitOptions,
    Role,
    copilot,
)

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

def _response_body(text: str) -> str:
    """Returns the text before a success tag; raises ValueError for missing or unsuccessful tags."""
    match = re.search(r"<completed>(.*?)</completed>", text, re.DOTALL)
    if match is None:
        raise ValueError("no <completed> tag in agent output")
    status = match.group(1).strip()
    if status.lower() != "success":
        raise ValueError(f"agent reported: {status}")
    return text[: match.start()].strip()


def _run_step(worktree: AgentWorkspace, profile: AgentProfile, prompt: str) -> str | None:
    """Runs a fresh session and returns its response body, or logs failure and returns None."""
    try:
        output = worktree.agent(profile).run(AgentRequest(prompt)).output
    except subprocess.CalledProcessError as exception:
        logger.error("agent command failed: %s; stderr: %s", exception, exception.stderr)
        return None
    try:
        return _response_body(output)
    except ValueError as exception:
        logger.error("%s", exception)
        return None


def _git_output(path: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", "--no-pager", *args], cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


def main(
    argv: list[str] | None = None,
    *,
    plan_model: str = PLAN_MODEL,
    plan_effort: str = PLAN_EFFORT,
    implement_model: str = IMPLEMENT_MODEL,
    implement_effort: str = IMPLEMENT_EFFORT,
    read_git: Callable[[Path, list[str]], str] = _git_output,
    new_agent: Callable[[], AgentBuilder] = Agent,
) -> int:
    """Plans and implements a task in one worktree; returns 0 on success, 1 on step failure, or 130 on interrupt."""
    parser = argparse.ArgumentParser(prog="plan_implement")
    parser.add_argument("task")
    args = parser.parse_args(argv)
    planner = AgentProfile(copilot, plan_model, plan_effort, name=Role.PLANNER)
    implementer = AgentProfile(copilot, implement_model, implement_effort, name=Role.DEVELOPER)
    branch = f"loop/run-{uuid.uuid4().hex[:8]}"
    logger.info("branch %s", branch)
    options = GitOptions(
        root_path=Path("workspace") / f"{Path.cwd().name}.worktrees",
        strategy=BranchStrategy(branch, "origin/main"),
    )

    with new_agent().with_git(options).open() as worktree:
        try:
            plan = _run_step(worktree, planner, f"{args.task}\n\n{PLAN_INSTRUCTIONS}")
            if not plan:
                logger.error("planning run failed: no usable plan")
                return 1

            head_before = read_git(worktree.path, ["rev-parse", "HEAD"])
            result = _run_step(worktree, implementer, f"{plan}\n\n{IMPLEMENT_INSTRUCTIONS}")
            commits = read_git(worktree.path, ["log", "--reverse", "--format=%H", f"{head_before}..HEAD"])
            logger.info("commits: %s", ", ".join(commits.splitlines()) or "none")
            if result is None:
                logger.error("implementing run failed")
                return 1
            return 0
        except KeyboardInterrupt:
            logger.warning("interrupted")
            return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
