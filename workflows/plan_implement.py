"""Example Workflow: plan a task with a strong model, implement it with a cheaper one, on one worktree.

One worktree, two stateless runs (an `AgentProfile` per step; the Shared Worktree Agent Run variant,
docs/concepts/str-agent-run.md). No push, pull request, or Ticket access.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loop import Agent, AgentBuilder, AgentProfile, AgentRequest, BranchStrategy, GitOptions, Worktree, copilot
from workflows.platforms.agent_response import extract_response
from workflows.platforms.git import WorkflowGit

logger = logging.getLogger("workflow.plan_implement")

PLAN_MODEL = "claude-opus-5.5"
PLAN_EFFORT = "max"
IMPLEMENT_MODEL = "claude-sonnet-5.5"
IMPLEMENT_EFFORT = "high"

PLAN_INSTRUCTIONS = """You are planning a coding task on this repository. Do not write or change any code.

Write a concise, actionable implementation plan for another agent to follow.

Reply with exactly one JSON object as your final message, with nothing after it:
{"status": "completed", "result": {"plan": "<the plan, as plain text>"}}

If you cannot produce a plan, reply instead with:
{"status": "failed", "result": {"reason": "<why>"}}
"""

IMPLEMENT_INSTRUCTIONS = """You are implementing a coding task on this repository, following the plan above.

Implement the plan. Commit your work with git. Do not push.

Reply with exactly one JSON object as your final message, with nothing after it:
{"status": "completed", "result": {}}

If you cannot complete the implementation, reply instead with:
{"status": "failed", "result": {"reason": "<why>"}}
"""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="plan_implement")
    parser.add_argument("task")
    return parser.parse_args(argv)


def _envelope(output: str) -> dict[str, Any] | None:
    """The last `{status, result}` envelope in `output`, or None when there is none of that shape."""
    response = extract_response(output)
    if response is None:
        return None
    envelope = json.loads(response)
    status, result = envelope.get("status"), envelope.get("result")
    if status not in ("completed", "failed") or not isinstance(result, dict):
        return None
    return envelope


def _run_step(worktree: Worktree, profile: AgentProfile, prompt: str) -> dict[str, Any] | None:
    """Runs one fresh, stateless step; its `result` object, or None when it failed or answered in the wrong shape."""
    try:
        output = worktree.agent(profile).run(AgentRequest(prompt)).output
    except subprocess.CalledProcessError:
        return None
    envelope = _envelope(output)
    if envelope is None or envelope["status"] != "completed":
        return None
    return envelope["result"]


def _run(worktree: Worktree, git: WorkflowGit, task: str, planner: AgentProfile, implementer: AgentProfile) -> int:
    # The agent-written plan and the task are embedded as text and sent verbatim; they are never preprocessed.
    plan_result = _run_step(worktree, planner, f"{task}\n\n{PLAN_INSTRUCTIONS}")
    if plan_result is None:
        logger.error("planning run failed")
        return 1
    plan = plan_result.get("plan")
    if not isinstance(plan, str) or not plan:
        logger.error("planning run returned an empty plan")
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
    git: WorkflowGit | None = None,
    new_agent: Callable[[], AgentBuilder] = Agent,
) -> int:
    """Plans `argv`'s task with `plan_model`, then implements it with `implement_model`; returns the exit code."""
    args = _parse_args(argv)
    git = git or WorkflowGit()
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
