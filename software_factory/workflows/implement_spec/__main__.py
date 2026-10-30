"""The implement_spec command: parse settings, build the services by hand, take up the open Specs and run the definition once for each."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from types import FrameType

from loop import Agent, LoopHook, WorktreeError, configure_logging

from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Ledger, LogSpans, RunResult, Services, Spans, StopSignal, Workflow, WorktreeGit, run
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.issues.errors import IssueError
from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.repo import GhCli, GitHubRepo
from software_factory.shared.process import CommandRunner, git_command
from software_factory.shared.telemetry import OtelSpans, OtlpObserverFactory, Tee, TracingObserver
from software_factory.stages.specs import post_failure_comment

from . import FAILURE_ERRORS, NAME, implement_spec
from .dry import SEAMS, Fakes
from .intake import Selection, release_held, select_specs, work_of
from .policy import SOURCE, SpecWork
from .settings import ImplementSpecSettings, Targets

logger = logging.getLogger("workflow.implement_spec")

LOG_DIR_NAME = ".loop"

LOG_LEVEL = "INFO"

LOOP_HOOKS: tuple[LoopHook, ...] = ()

MAX_TICKET_FAILURES = 2

DRY_OTLP_ENDPOINT = "http://localhost:4318"

HARNESS_SLUG = "antshc/loop"

_ROOT = Path(__file__).resolve().parents[3]

_INTERRUPTED = 130

_SPAN_LOGGER = "software_factory.runtime"


@dataclass(frozen=True)
class Runtime:
    """How the run is recorded, observed and stopped; a test passes its own among the services."""

    ledger: Ledger
    spans: Spans
    stop: StopSignal


class InterruptRequest:
    """Set by SIGINT or SIGTERM; the run stops between Tickets, and a second signal takes the default action."""

    def __init__(self) -> None:
        self._requested = False

    def requested(self) -> bool:
        """Whether an interrupt arrived."""
        return self._requested

    def request(self, signum: int, frame: FrameType | None) -> None:
        """The signal handler: ask for a stop and give the signals back to their default action."""
        self._requested = True
        logger.warning("interrupt requested; stopping after the current Ticket")
        for name in (signal.SIGINT, signal.SIGTERM):
            signal.signal(name, signal.SIG_DFL)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="implement_spec")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
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
        help="replace this external dependency with a fake, repeatable; agent needs no CLI, git no repository, github no network, store no ledger files",
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


def harness_target() -> RepositoryTarget:
    """The harness repository as the one target, with every optional activity on."""
    return RepositoryTarget(HARNESS_SLUG, _ROOT, _ROOT / "workspace" / f"{_ROOT.name}.worktrees")


def build_services(fakes: Fakes, observer: TracingObserver | None) -> Services:
    """The services of the definition, each real unless `fakes` replaces it."""
    run_process = CommandRunner()
    harness = harness_target()
    gh = GhCli(run_process, cwd=harness.checkout)
    dry_git = fakes.git()
    git, worktree_git = dry_git if dry_git is not None else (GitClient(run_process), WorktreeGit(git_command))
    repository = fakes.github() or GitHubRepo.from_slug(harness.slug, gh)
    github = GitHub(fakes.gh if "github" in fakes.seams else gh)
    launcher = AgentLauncher(fakes.new_agent() or Agent, observer, fakes.readers())
    return Services(Issues(repository), github, launcher).bind(GitClient, git).bind(WorktreeGit, worktree_git)


def _runtime(settings: ImplementSpecSettings, fakes: Fakes, observer: TracingObserver | None, stop: StopSignal) -> Runtime:
    ledger = Ledger(fakes.ledger_root() or settings.ledger_root)
    spans: Spans = LogSpans() if observer is None else Tee(LogSpans(), OtelSpans(observer))
    return Runtime(ledger, spans, stop)


def main(argv: Sequence[str] | None = None, *, services: Services | None = None) -> int:
    """Delivers harness Specs through the configured repository; `--fake <seam>` or `--dry` replaces external dependencies with fakes, and `services` replaces them all."""
    args = _parse_args(argv)
    fakes = Fakes(args.fake, speed=args.fake_speed, rework=not args.no_rework)
    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    _configure_logging(args.log_level, log_dir)
    settings = ImplementSpecSettings(
        log_dir=log_dir,
        spec_number=args.spec,
        max_failures=MAX_TICKET_FAILURES,
        loop_hooks=LOOP_HOOKS,
        otlp_endpoint=args.otlp_endpoint,
    )
    try:
        return _serve(settings, fakes, services)
    except ImportError as exception:
        logger.error("implement_spec unavailable: %s", exception)
        return 1
    except KeyboardInterrupt:
        logger.warning("implement_spec interrupted")
        return _INTERRUPTED
    except Exception as exception:  # last-resort boundary: log and fail, never crash bare
        logger.error("implement_spec failed (%s); enable DEBUG for details", type(exception).__name__)
        logger.debug("unexpected error: %s", exception, exc_info=True)
        return 1


def _serve(settings: ImplementSpecSettings, fakes: Fakes, given: Services | None) -> int:
    """Builds what the caller did not pass, then runs every Spec once."""
    interrupt = InterruptRequest()
    observer = _observer(settings) if given is None else None
    services = given or build_services(fakes, observer)
    runtime = services.get(Runtime) if Runtime in services.types() else _runtime(settings, fakes, observer, interrupt)
    if given is None:
        return _run_interruptible(settings, services, runtime, interrupt)
    return _run_specs(settings, services, runtime)


def _observer(settings: ImplementSpecSettings) -> TracingObserver | None:
    return OtlpObserverFactory(NAME, resource_per_agent=True)(settings.otlp_endpoint) if settings.otlp_endpoint else None


def _run_interruptible(settings: ImplementSpecSettings, services: Services, runtime: Runtime, interrupt: InterruptRequest) -> int:
    """Runs the Specs with SIGINT and SIGTERM asking the run to stop between Tickets."""
    handlers = {name: signal.signal(name, interrupt.request) for name in (signal.SIGINT, signal.SIGTERM)}
    try:
        return _run_specs(settings, services, runtime)
    finally:
        for name, handler in handlers.items():
            signal.signal(name, handler)


def _run_specs(settings: ImplementSpecSettings, services: Services, runtime: Runtime) -> int:
    """Takes up the open Specs and runs the definition once for each; the exit code is the worst of their results."""
    targets = services.get(Targets) if Targets in services.types() else Targets((harness_target(),))
    definition = implement_spec(settings, targets)
    selection = select_specs(services.get(Issues), settings.spec_number)
    if settings.spec_number is not None and not _found(selection):
        logger.error("no open Spec #%s on %s", settings.spec_number, HARNESS_SLUG)
        return 1
    code = 1 if selection.unreadable else 0
    for spec in selection.runnable:
        if runtime.stop.requested():
            return _INTERRUPTED
        release_held(runtime.ledger, NAME, spec)
        result = _run_spec(definition, spec, services, runtime)
        if result == _INTERRUPTED:
            return _INTERRUPTED
        code = max(code, result)
    return code


def _found(selection: Selection) -> bool:
    return bool(selection.runnable or selection.unreadable or selection.awaiting_human)


def _run_spec(definition: Workflow[SpecWork], spec: SpecGraphSnapshot, services: Services, runtime: Runtime) -> int:
    """The exit code of one Spec's run; a worktree that cannot be closed clean, or a declared error outside any Stage such as a failing worktree hook, fails the Spec, not the whole intake."""
    try:
        result = run(definition, work_of(spec), services=services, ledger=runtime.ledger, spans=runtime.spans, stop=runtime.stop)
    except WorktreeError as exception:
        logger.error("Spec #%s failed: %s", spec.number, exception)
        return 1
    except FAILURE_ERRORS as exception:
        logger.error("Spec #%s failed: %s", spec.number, exception)
        _comment_failure(services.get(Issues), spec.number)
        return 1
    _report(spec, result)
    return result.exit_code


def _comment_failure(tracker: Issues, number: int) -> None:
    try:
        post_failure_comment(tracker, number, source=SOURCE)
    except (IssueError, CalledProcessError) as exception:
        logger.error("Spec #%s: the failure could not be commented: %s", number, exception)


def _report(spec: SpecGraphSnapshot, result: RunResult) -> None:
    logger.info("Spec #%s %s%s", spec.number, result.status, f": {result.reason}" if result.reason else "")


def _configure_logging(level: str, log_dir: Path) -> None:
    # The Workflow is the application: it owns the root logger so its own records land in the same file.
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
        record.name == _SPAN_LOGGER or (record.name == "workflow.implement_spec" and record.levelno >= logging.WARNING)
    )


if __name__ == "__main__":
    raise SystemExit(main())
