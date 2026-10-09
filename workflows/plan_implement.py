"""Example Workflow: plan a task with a strong model, implement it with a cheaper one, on one worktree.

One agent runner, two stateless runs (the per-run `model`/`reasoning_effort` arguments; the Shared
Worktree Agent Run variant, docs/concepts/str-agent-run.md). No push, pull request, or Ticket access.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from loop import (
    AgentClientFactory,
    AgentRunner,
    AgentRunnerProvider,
    AgentRunResult,
    Git,
    InMemorySessionStore,
    Prompt,
    RepositoryData,
    copilot,
)

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


def _envelope(response: str) -> dict[str, Any] | None:
    """The decoded `{status, result}` envelope, or None when the response is not that shape."""
    try:
        envelope = json.loads(response)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    status, result = envelope.get("status"), envelope.get("result")
    if status not in ("completed", "failed") or not isinstance(result, dict):
        return None
    return envelope


def _run_step(
    runner: AgentRunner,
    prompt: Prompt,
    model: str,
    reasoning_effort: str,
) -> tuple[AgentRunResult, dict[str, Any] | None]:
    """Runs one fresh, stateless step; its `result` object, or None when it failed or answered in the wrong shape."""
    run = runner.run(prompt, model, reasoning_effort)
    outcome = run.result
    if not outcome.success:
        return run, None
    envelope = _envelope(outcome.response)
    if envelope is None:
        return run, None
    return run, envelope["result"]


def _repository(harness_root: Path) -> RepositoryData:
    # owner_repo is unused by this Workflow (no GitHub access); the harness folder name is a stable placeholder.
    return RepositoryData(
        path=harness_root,
        owner_repo=f"local/{harness_root.name}",
        is_harness=True,
        worktree_root=harness_root / "workspace" / f"{harness_root.name}.worktrees",
    )


def _run(
    runner: AgentRunner,
    task: str,
    plan_model: str,
    plan_effort: str,
    implement_model: str,
    implement_effort: str,
) -> int:
    # The agent-written plan and the task are embedded as text and sent verbatim; they are never preprocessed.
    plan_prompt = Prompt(f"{task}\n\n{PLAN_INSTRUCTIONS}")
    _, plan_result = _run_step(runner, plan_prompt, plan_model, plan_effort)
    if plan_result is None:
        logger.error("planning run failed")
        return 1
    plan = plan_result.get("plan")
    if not isinstance(plan, str) or not plan:
        logger.error("planning run returned an empty plan")
        return 1

    implement_prompt = Prompt(f"{plan}\n\n{IMPLEMENT_INSTRUCTIONS}")
    implement_run, implement_result = _run_step(runner, implement_prompt, implement_model, implement_effort)
    logger.info("commits: %s", ", ".join(implement_run.commits) or "none")
    if implement_result is None:
        logger.error("implementing run failed")
        return 1
    return 0


def main(
    argv: list[str] | None = None,
    *,
    plan_model: str = PLAN_MODEL,
    plan_effort: str = PLAN_EFFORT,
    implement_model: str = IMPLEMENT_MODEL,
    implement_effort: str = IMPLEMENT_EFFORT,
    harness_root: Path | None = None,
    git: Git | None = None,
    agent_factory: AgentClientFactory | None = None,
) -> int:
    """Plans `argv`'s task with `plan_model`, then implements it with `implement_model`; returns the exit code."""
    args = _parse_args(argv)
    root = (harness_root or Path.cwd()).resolve()
    git = git or Git()
    agent_factory = agent_factory or copilot(InMemorySessionStore())
    provider = AgentRunnerProvider(git, root, agent_factory)
    runner = provider.create(_repository(root), base="main")

    try:
        return _run(runner, args.task, plan_model, plan_effort, implement_model, implement_effort)
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130
    finally:
        logger.info("branch %s", runner.branch)
        keep = runner.lifecycle.has_changes()
        if keep:
            logger.warning("worktree kept at %s", runner.worktree.path)
        runner.exit(keep_worktree=keep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
