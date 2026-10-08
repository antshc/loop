"""Autonomous dev loop: one branch and worktree per open Spec, one Ticket per fresh agent run.

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
    CommandExecutor,
    ExecutionStore,
    FileExecutionStore,
    Git,
    Hook,
    InMemorySessionStore,
    configure_logging,
    copilot,
)

from workflows.platforms.work_tracking import (
    GitHubClient,
    RepositoryConfig,
    RepositoryPool,
    RepositoryPoolError,
    TicketsTracker,
)

from workflows.platforms.work_tracking import WorkIdentifier

from .app import process_specs
from .deps import DevDeps, GithubFactory
from .prompting import prompt_args
from .result import DevResult, DevResultError, parse_dev_result
from .settings import HOOKS, LOG_DIR_NAME, LOG_LEVEL, PROMPT, REPOSITORIES

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
    parser.add_argument("--harness-root", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def _build_deps(
    harness_root: Path,
    repository_pool: RepositoryPool,
    log_dir: Path,
    *,
    git: Git,
    github_factory: GithubFactory,
    agent_factory: AgentClientFactory | None,
    executor: CommandExecutor | None,
    store: ExecutionStore | None,
    hooks: Sequence[Hook],
    cancel: threading.Event | None,
) -> DevDeps:
    return DevDeps(
        harness_root=harness_root,
        repository_pool=repository_pool,
        tracker=TicketsTracker(
            github_factory(harness_root), store or FileExecutionStore(log_dir), repository_pool.harness.owner_repo
        ),
        git=git,
        agent_factory=agent_factory or copilot(InMemorySessionStore()),
        executor=executor,
        hooks=hooks,
        template=PROMPT.read_text(),
        cancel=cancel or threading.Event(),
    )


def main(
    argv: list[str] | None = None,
    *,
    git: Git | None = None,
    github_factory: GithubFactory | None = None,
    repositories: Sequence[RepositoryConfig] = REPOSITORIES,
    agent_factory: AgentClientFactory | None = None,
    executor: CommandExecutor | None = None,
    store: ExecutionStore | None = None,
    hooks: Sequence[Hook] = HOOKS,
    cancel: threading.Event | None = None,
) -> int:
    """Runs the dev Workflow once over every open Spec; returns the process exit code."""
    args = _parse_args(argv)

    harness_root = (args.harness_root or Path.cwd()).resolve()
    log_dir = (args.log_dir or harness_root / LOG_DIR_NAME).resolve()
    configure_logging(log_dir / "dev.log", args.log_level)

    git = git or Git()
    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout)[0])
    try:
        repository_pool = RepositoryPool(repositories, github_factory)
    except RepositoryPoolError as exception:
        logger.error("repository pool misconfigured: %s", exception)
        return 1

    deps = _build_deps(
        harness_root,
        repository_pool,
        log_dir,
        git=git,
        github_factory=github_factory,
        agent_factory=agent_factory,
        executor=executor,
        store=store,
        hooks=hooks,
        cancel=cancel,
    )

    try:
        return process_specs(deps)
    except Exception as exception:  # last-resort boundary the ticket requires: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1
