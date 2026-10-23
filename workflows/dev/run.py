"""Settings, dependency wiring, and the dev entry point."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from loop import Agent, AgentBuilder, LoopHook, configure_logging, otlp_observer

from .backlog import DevDeps, process_specs
from .checkout import GitService
from .journal import FileExecutionStore
from .platforms import GithubFactory, TicketsTracker, github_for
from .repositories import HARNESS, RepositoryWorkflow, configured_repositories
from .telemetry import SpanFactory, TracingObserver, no_span


logger = logging.getLogger("workflow.dev")


LOG_DIR_NAME = ".loop"


LOG_LEVEL = "INFO"


LOOP_HOOKS: tuple[LoopHook, ...] = ()


MAX_TICKET_FAILURES = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    parser.add_argument(
        "--otlp-endpoint",
        default=None,
        help="export traces and metrics over OTLP/HTTP to this base URL, e.g. http://localhost:4318 (needs loop[otel])",
    )
    return parser.parse_args(argv)


def _observer_for(endpoint: str) -> TracingObserver:
    return otlp_observer(endpoint=endpoint, service_name="dev")


def _observe(
    new_agent: Callable[[], AgentBuilder], endpoint: str | None, new_observer: Callable[[str], TracingObserver]
) -> tuple[Callable[[], AgentBuilder], SpanFactory]:
    """Agents and Workflow spans that share one observer; the plain pair when no endpoint is given."""
    if endpoint is None:
        return new_agent, no_span
    observer = new_observer(endpoint)
    return (lambda: new_agent().with_observer(observer)), observer.span


def main(
    argv: list[str] | None = None,
    *,
    git: GitService | None = None,
    github_factory: GithubFactory | None = None,
    new_agent: Callable[[], AgentBuilder] = Agent,
    store: FileExecutionStore | None = None,
    loop_hooks: tuple[LoopHook, ...] = LOOP_HOOKS,
    repositories: Sequence[RepositoryWorkflow] | None = None,
    new_observer: Callable[[str], TracingObserver] = _observer_for,
) -> int:
    """Delivers harness Specs through configured repositories, defaulting to implementation on the harness."""
    args = _parse_args(argv)

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    # The workflow is the application: it owns the root logger so its own records land in the same file.
    configure_logging(args.log_level, log_file=log_dir / "dev.log", logger="")

    try:
        github = github_for(HARNESS, github_factory)
        if repositories is None:
            repositories = configured_repositories(HARNESS)
        agents, span = _observe(new_agent, args.otlp_endpoint, new_observer)
        deps = DevDeps(
            tracker=TicketsTracker(HARNESS.owner_name.owner, HARNESS.owner_name.repo, gh=github.run),
            store=store or FileExecutionStore(log_dir),
            git=git or GitService(),
            new_agent=agents,
            loop_hooks=loop_hooks,
            ticket_repository=HARNESS.owner_name,
            github_factory=github_factory,
            max_ticket_failures=MAX_TICKET_FAILURES,
            span=span,
        )
        return process_specs(repositories, deps)
    except Exception as exception:  # last-resort boundary: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1
