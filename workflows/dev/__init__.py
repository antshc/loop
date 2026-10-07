"""Autonomous dev loop: one branch, worktree, and sandbox per open Spec, one Ticket per fresh agent run.

Control flow, metadata parsing, and the dev result model are owned here,
not by the loop library (see docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

import argparse
import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from loop import (
    AgentClientFactory,
    BranchService,
    CommitService,
    ExecutionStore,
    FileExecutionStore,
    Hook,
    InMemorySessionStore,
    SandboxFactory,
    WorktreeService,
    configure_logging,
    copilot,
    origin_slug,
)

from workflows.platforms.work_tracking import GitHubClient, TicketsTracker

from .app import process_specs
from .deps import DevDeps, GithubFactory
from .prompting import prompt_args
from .result import DevResult, DevResultError, parse_dev_result
from .settings import HOOKS, LOG_DIR_NAME, LOG_LEVEL, PROMPT, SANDBOX_FACTORY

__all__ = [
    "PROMPT",
    "DevResult",
    "DevResultError",
    "main",
    "parse_dev_result",
    "prompt_args",
]

logger = logging.getLogger("workflow.dev")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--harness-root", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def _build_deps(
    harness_root: Path,
    harness_slug: str,
    log_dir: Path,
    *,
    branches: BranchService | None,
    commits: CommitService | None,
    worktrees: WorktreeService | None,
    github_factory: GithubFactory | None,
    agent_factory: AgentClientFactory | None,
    sandbox_factory: SandboxFactory | None,
    store: ExecutionStore | None,
    hooks: Sequence[Hook],
    cancel: threading.Event | None,
) -> DevDeps:
    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout)[0])
    return DevDeps(
        harness_root=harness_root,
        harness_slug=harness_slug,
        tracker=TicketsTracker(github_factory(harness_root), store or FileExecutionStore(log_dir), harness_slug),
        github_factory=github_factory,
        branches=branches or BranchService(),
        commits=commits or CommitService(),
        worktrees=worktrees or WorktreeService(),
        agent_factory=agent_factory or copilot(InMemorySessionStore()),
        sandbox_factory=sandbox_factory or SANDBOX_FACTORY,
        hooks=hooks,
        template=PROMPT.read_text(),
        cancel=cancel or threading.Event(),
    )


def main(
    argv: list[str] | None = None,
    *,
    branches: BranchService | None = None,
    commits: CommitService | None = None,
    worktrees: WorktreeService | None = None,
    github_factory: GithubFactory | None = None,
    agent_factory: AgentClientFactory | None = None,
    sandbox_factory: SandboxFactory | None = None,
    store: ExecutionStore | None = None,
    hooks: Sequence[Hook] = HOOKS,
    cancel: threading.Event | None = None,
) -> int:
    """Runs the dev Workflow once over every open Spec; returns the process exit code."""
    args = _parse_args(argv)

    harness_root = (args.harness_root or Path.cwd()).resolve()
    log_dir = (args.log_dir or harness_root / LOG_DIR_NAME).resolve()
    configure_logging(log_dir / "dev.log", args.log_level)

    harness_slug = origin_slug(harness_root)
    if harness_slug is None:
        logger.error("harness root is not a resolvable github.com git repository: %s", harness_root)
        return 1

    deps = _build_deps(
        harness_root,
        harness_slug,
        log_dir,
        branches=branches,
        commits=commits,
        worktrees=worktrees,
        github_factory=github_factory,
        agent_factory=agent_factory,
        sandbox_factory=sandbox_factory,
        store=store,
        hooks=hooks,
        cancel=cancel,
    )

    try:
        return process_specs(deps)
    except Exception as exception:  # last-resort boundary the ticket requires: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1
