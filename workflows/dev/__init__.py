"""Autonomous dev loop: one branch and worktree per open Spec, one Ticket per fresh agent run.

Control flow, metadata parsing, and the dev result model are owned here,
not by the loop library (see docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from loop import Agent, AgentBuilder, LoopHook
from workflows.platforms.git import WorkflowGit
from workflows.platforms.work_tracking import (
    GitHubClient,
    RepositoryConfig,
    RepositoryPool,
    RepositoryPoolError,
    TicketsTracker,
    WorkIdentifier,
)

from .deps import DevDeps, GithubFactory, Prompts
from .logging_config import configure_logging
from .prompting import prompt_args
from .result import DevResult, DevResultError, parse_dev_result
from .settings import LOG_DIR_NAME, LOG_LEVEL, LOOP_HOOKS, PROMPT, REPOSITORIES
from .store import FileExecutionStore
from .workflow import DevWorkflow

__all__ = [
    "PROMPT",
    "DevResult",
    "DevResultError",
    "WorkIdentifier",
    "main",
    "parse_dev_result",
    "prompt_args",
]

logger = logging.getLogger("workflow.dev")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    git: WorkflowGit | None = None,
    github_factory: GithubFactory | None = None,
    repositories: Sequence[RepositoryConfig] = REPOSITORIES,
    new_agent: Callable[[], AgentBuilder] = Agent,
    store: FileExecutionStore | None = None,
    loop_hooks: tuple[LoopHook, ...] = LOOP_HOOKS,
) -> int:
    """Runs the dev Workflow once over every open Spec from the current folder; returns the process exit code."""
    args = _parse_args(argv)

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    configure_logging(log_dir / "dev.log", args.log_level)

    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout)[0])
    try:
        repository_pool = RepositoryPool(repositories, github_factory)
    except RepositoryPoolError as exception:
        logger.error("repository pool misconfigured: %s", exception)
        return 1

    deps = DevDeps(
        repository_pool=repository_pool,
        tracker=TicketsTracker(github_factory(repository_pool.harness.path)),
        store=store or FileExecutionStore(log_dir),
        git=git or WorkflowGit(),
        new_agent=new_agent,
        loop_hooks=loop_hooks,
        prompts=Prompts(dev=PROMPT.read_text()),
    )

    try:
        return DevWorkflow(deps).process_specs()
    except Exception as exception:  # last-resort boundary the ticket requires: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1
