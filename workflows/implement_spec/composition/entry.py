"""The implement_spec command: parse settings, build the composition root, and run every Spec once."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from dishka import Container, Provider, make_container

from loop import LoopHook, Phases, configure_logging

from workflows.shared.providers import SharedProvider
from workflows.shared.telemetry import Telemetry

from ..backlog.router import SpecRouter
from ..model.definition import implement_spec_workflow
from ..model.repositories import Targets, Tracker
from ..model.settings import ImplementSpecSettings
from .dry import SEAMS, Fakes
from .providers import ImplementSpecProvider


logger = logging.getLogger("workflow.implement_spec")


LOG_DIR_NAME = ".loop"


LOG_LEVEL = "INFO"


LOOP_HOOKS: tuple[LoopHook, ...] = ()


MAX_TICKET_FAILURES = 2


DRY_OTLP_ENDPOINT = "http://localhost:4318"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="implement_spec")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    parser.add_argument("--visualize", action="store_true", help="show the phase table (needs loop[visualization])")
    parser.add_argument("--spec", type=int, default=None, help="deliver only this Spec number")
    parser.add_argument("--no-otel", action="store_true", help="disable telemetry, including for --dry")
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
    if args.no_otel:
        args.otlp_endpoint = None
    if "git" in args.fake and "agent" not in args.fake:
        parser.error("--fake git needs --fake agent: a real agent would commit where the fake git cannot see")
    return args


def _dry_endpoint(endpoint: str | None) -> str:
    return endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or DRY_OTLP_ENDPOINT


def build_container(settings: ImplementSpecSettings, *providers: Provider) -> Container:
    """The process container; `providers` override any default registration, the last one winning."""
    return make_container(SharedProvider(), ImplementSpecProvider(), *providers, context={ImplementSpecSettings: settings})


def run_workflow(container: Container, settings: ImplementSpecSettings) -> int:
    """Runs every Spec once inside one REQUEST scope that shares the Workflow's phases."""
    # Resolved before the Workflow opens, so a broken tracker or configuration fails without a trace.
    container.get(Tracker)
    container.get(Targets)
    observer = container.get(Telemetry).observer
    with implement_spec_workflow(observer=observer, visualize=settings.visualize).run() as phases:
        with container(context={Phases: phases}) as request:
            code = request.get(SpecRouter).run()
        if code:
            phases.fail("A Spec failed or needs human input")
        return code


def main(argv: Sequence[str] | None = None, *, providers: Sequence[Provider] = ()) -> int:
    """Delivers harness Specs through configured repositories; `--fake <seam>` or `--dry` replaces external dependencies with fakes."""
    args = _parse_args(argv)
    fakes = Fakes(args.fake, speed=args.fake_speed, rework=not args.no_rework)
    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    _configure_logging(args.log_level, log_dir)
    settings = ImplementSpecSettings(
        log_dir=log_dir,
        spec_number=args.spec,
        max_ticket_failures=MAX_TICKET_FAILURES,
        loop_hooks=LOOP_HOOKS,
        visualize=args.visualize,
        otlp_endpoint=args.otlp_endpoint,
    )
    try:
        container = build_container(settings, fakes.provider(), *providers)
        try:
            return run_workflow(container, settings)
        finally:
            container.close()
    except ImportError as exception:
        logger.error("implement_spec unavailable: %s", exception)
        return 1
    except Exception as exception:  # last-resort boundary: log and fail, never crash bare
        logger.error("implement_spec failed (%s); enable DEBUG for details", type(exception).__name__)
        logger.debug("unexpected error: %s", exception, exc_info=True)
        return 1


def _configure_logging(level: str, log_dir: Path) -> None:
    # The workflow is the application: it owns the root logger so its own records land in the same file.
    configure_logging(level, log_file=log_dir / "implement_spec.log", logger="")
    library_logger = logging.getLogger("loop")
    library_logger.handlers.clear()
    library_logger.setLevel(logging.NOTSET)
    library_logger.propagate = True
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.addFilter(_high_level_log)


def _high_level_log(record: logging.LogRecord) -> bool:
    return record.levelno >= logging.INFO and (
        record.name == "loop.workflow" or
        (record.name == "workflow.implement_spec" and record.levelno >= logging.WARNING)
    )
