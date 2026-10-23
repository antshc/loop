"""Settings, dependency wiring, and the dev entry point."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from loop import Agent, AgentBuilder, LoopHook, SessionLogReader, configure_logging, otlp_observer

from .backlog import DevDeps, process_specs
from .checkout import GitService
from .dry import SEAMS, Fakes
from .journal import FileExecutionStore
from .platforms import GithubFactory, TicketsTracker, github_for
from .repositories import HARNESS, RepositoryWorkflow, configured_repositories
from .telemetry import SpanFactory, TracingObserver, no_span


logger = logging.getLogger("workflow.dev")


LOG_DIR_NAME = ".loop"


LOG_LEVEL = "INFO"


LOOP_HOOKS: tuple[LoopHook, ...] = ()


MAX_TICKET_FAILURES = 2


DRY_OTLP_ENDPOINT = "http://localhost:4318"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    parser.add_argument(
        "--otlp-endpoint",
        default=None,
        help="export traces and metrics over OTLP/HTTP to this base URL, e.g. http://localhost:4318 (needs loop[otel])",
    )
    parser.add_argument(
        "--fake",
        action="append",
        default=[],
        choices=SEAMS,
        help="replace this external dependency with a fake, repeatable; agent needs no CLI, git no repository, github no network, store no log files",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help=f"fake every dependency and publish telemetry to the dashboard (OTLP endpoint defaults to {DRY_OTLP_ENDPOINT})",
    )
    parser.add_argument("--fake-speed", type=float, default=1.0, help="delay multiplier for the fake agent; 0 is instant")
    parser.add_argument("--no-rework", action="store_true", help="the fake agent never fails a first attempt")
    args = parser.parse_args(argv)
    if args.dry:
        args.fake = list(SEAMS)
        args.otlp_endpoint = _dry_endpoint(args.otlp_endpoint)
    if "git" in args.fake and "agent" not in args.fake:
        parser.error("--fake git needs --fake agent: a real agent would commit where the fake git cannot see")
    return args


def _dry_endpoint(endpoint: str | None) -> str:
    return endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or DRY_OTLP_ENDPOINT


def _observer_for(endpoint: str) -> TracingObserver:
    return otlp_observer(endpoint=endpoint, service_name="dev", resource_per_agent=True)


def _observe(
    new_agent: Callable[[], AgentBuilder],
    endpoint: str | None,
    new_observer: Callable[[str], TracingObserver],
    readers: Sequence[SessionLogReader] | None,
) -> tuple[Callable[[], AgentBuilder], SpanFactory]:
    """Agents and Workflow spans that share one observer; the plain pair when no endpoint is given."""
    if endpoint is None:
        return new_agent, no_span
    observer = new_observer(endpoint)
    return (lambda: new_agent().with_observer(observer, readers=readers)), observer.span


def main(
    argv: list[str] | None = None,
    *,
    git: GitService | None = None,
    github_factory: GithubFactory | None = None,
    new_agent: Callable[[], AgentBuilder] | None = None,
    store: FileExecutionStore | None = None,
    loop_hooks: tuple[LoopHook, ...] = LOOP_HOOKS,
    repositories: Sequence[RepositoryWorkflow] | None = None,
    new_observer: Callable[[str], TracingObserver] = _observer_for,
    readers: Sequence[SessionLogReader] | None = None,
) -> int:
    """Delivers harness Specs through configured repositories, defaulting to implementation on the harness; `--fake <seam>` or `--dry` replaces external dependencies with fakes."""
    args = _parse_args(argv)
    fakes = Fakes(args.fake, speed=args.fake_speed, rework=not args.no_rework)
    git = git or fakes.git()
    github_factory = github_factory or fakes.github_factory()
    new_agent = new_agent or fakes.new_agent()
    readers = readers if readers is not None else fakes.readers()
    store = store or fakes.store()

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    # The workflow is the application: it owns the root logger so its own records land in the same file.
    configure_logging(args.log_level, log_file=log_dir / "dev.log", logger="")

    try:
        github = github_for(HARNESS, github_factory)
        if repositories is None:
            repositories = configured_repositories(HARNESS)
        agents, span = _observe(new_agent or Agent, args.otlp_endpoint, new_observer, readers)
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
