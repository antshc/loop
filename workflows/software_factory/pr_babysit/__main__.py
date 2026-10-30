"""The pr_babysit command: discover the user's pull requests continuously, or maintain one explicit pull request, through the factory runtime."""

from __future__ import annotations

import argparse
import logging
import re
import signal
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired
from types import FrameType
from typing import Any

from loop import Agent

from workflows.software_factory.events.pull_requests import PullRequestWork
from loop import DefinitionError, Fail, Ledger, LogSpans, RunResult, Services, Spans, StopSignal, Verify, WorktreeGit, run
from workflows.software_factory.shared.agents.launcher import AgentLauncher
from loop.shared.git import GitClient
from loop.shared.github.account import GitHub
from loop.shared.github.repo import GhCli
from workflows.software_factory.shared.pacing import Pacer
from loop.shared.process import CommandRunner, git_command
from workflows.software_factory.shared.telemetry import OtelSpans, OtlpObserverFactory, Tee, TracingObserver
from workflows.software_factory.stages import pull_requests
from workflows.software_factory.stages.checks import as_verify

from . import HANDED_OFF_ERRORS, NAME, pr_babysit
from .intake import ChildLauncher, DiscoveryRequest, ProcessLauncher, Scheduler, adopt_state, release_if_changed, work_of
from .policy import FINGERPRINT, REASON, Blocked, fingerprint
from .settings import Repository, Settings

logger = logging.getLogger("workflow.pr_babysit")

_PULL_REQUEST = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)/?")

_GIT_TIMEOUT_S = 120

_INTERRUPTED = 130


@dataclass(frozen=True)
class Runtime:
    """How the runs are recorded, observed and stopped; a test passes its own among the services."""

    ledger: Ledger
    spans: Spans
    stop: StopSignal


class StopRequest:
    """Set by SIGINT or SIGTERM: runs stop between polls, a waiting pacer wakes, and a second signal takes the default action."""

    def __init__(self) -> None:
        self._requested = threading.Event()

    def requested(self) -> bool:
        """Whether an interrupt arrived."""
        return self._requested.is_set()

    def wait(self, seconds: float) -> None:
        """Waits `seconds`, or until an interrupt arrives."""
        self._requested.wait(seconds)

    def request(self, signum: int, frame: FrameType | None) -> None:
        """The signal handler: ask for a stop and give the signals back to their default action."""
        self._requested.set()
        logger.warning("interrupt requested; stopping after the current poll")
        for name in (signal.SIGINT, signal.SIGTERM):
            signal.signal(name, signal.SIG_DFL)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr_babysit", description="Discover PRs continuously or babysit one PR")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pr", help="https://github.com/owner/repo/pull/123")
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--retry", help="Release an enrolled blocked PR during discovery, so its work starts afresh")
    return parser


def verify_of(repository: Repository, timeout: float, run_process: CommandRunner) -> Verify:
    """The repository's build, then its test command, as one check on a worktree; the first failure is the result."""
    steps = [as_verify(command, timeout_s=timeout, run=run_process) for command in (repository.build, repository.test)]

    def verify(path: Path) -> Fail[Any] | None:
        for step in steps:
            failed = step(path)
            if failed is not None:
                return failed
        return None

    return verify


def build_services(observer: TracingObserver | None, stop: StopRequest) -> Services:
    """The services of the definition and the scheduler, all real."""
    run_process = CommandRunner(timeout_s=_GIT_TIMEOUT_S)
    return (
        Services(GitClient(run_process), GitHub(GhCli(run_process)), AgentLauncher(Agent, observer), Pacer(stop.wait))
        .bind(WorktreeGit, WorktreeGit(git_command))
    )


def _runtime(settings: Settings, observer: TracingObserver | None, stop: StopSignal) -> Runtime:
    spans: Spans = LogSpans() if observer is None else Tee(LogSpans(), OtelSpans(observer))
    return Runtime(Ledger(settings.ledger_root), spans, stop)


def main(argv: Sequence[str] | None = None, *, services: Services | None = None) -> int:
    """Starts discovery, or maintains one explicit pull request; `services` replaces every external dependency."""
    parser = _parser()
    args = parser.parse_args(argv)
    match = _PULL_REQUEST.fullmatch(args.pr or "")
    if args.pr and not match:
        parser.error("--pr must be a GitHub PR URL")
    if args.pr and (args.include_existing or args.retry):
        parser.error("--include-existing and --retry apply to discovery only")
    logging.basicConfig(level=logging.INFO)
    try:
        settings = Settings.load(args.config)
        if match:
            return _maintain(settings, services, match[1], int(match[2]))
        return _discover(settings, services, DiscoveryRequest(args.config.resolve(), args.include_existing, args.retry))
    except (Blocked, ValueError, OSError) as exception:
        logger.error("PR maintenance stopped: %s", exception)
        return 1
    except KeyboardInterrupt:
        logger.warning("pr_babysit interrupted")
        return _INTERRUPTED


def _maintain(settings: Settings, given: Services | None, slug: str, number: int) -> int:
    """Maintains one pull request until it closes, blocks or is interrupted; the exit code of its run."""
    stop = StopRequest()
    observer = _observer(settings) if given is None else None
    services = given or build_services(observer, stop)
    runtime = _runtime_of(settings, services, observer, stop)
    work = work_of(settings, slug, number)
    github = services.get(GitHub)
    adopt_state(settings, runtime.ledger, github, work)
    release_if_changed(runtime.ledger, github, work)
    definition = pr_babysit(settings, verify_of(settings.repository(slug), settings.timeout, CommandRunner()))
    with _handling(stop, given is None):
        try:
            result = run(definition, work, services=services, ledger=runtime.ledger, spans=runtime.spans, stop=runtime.stop)
        except DefinitionError:
            raise
        except HANDED_OFF_ERRORS as exception:
            return _hand_off_unclosed(runtime.ledger, github, work, str(exception))
    _report(work, result)
    return result.exit_code


def _discover(settings: Settings, given: Services | None, request: DiscoveryRequest) -> int:
    """Scans for pull requests and supervises one child process for each, until asked to stop."""
    stop = StopRequest()
    services = given or build_services(None, stop)
    runtime = _runtime_of(settings, services, None, stop)
    children = services.get(ChildLauncher) if ChildLauncher in services.types() else ProcessLauncher()
    scheduler = Scheduler(settings, request, services.get(GitHub), runtime.ledger, children, services.get(Pacer), runtime.stop)
    with _handling(stop, given is None):
        return scheduler.serve()


def _runtime_of(settings: Settings, services: Services, observer: TracingObserver | None, stop: StopSignal) -> Runtime:
    return services.get(Runtime) if Runtime in services.types() else _runtime(settings, observer, stop)


def _observer(settings: Settings) -> TracingObserver | None:
    if not settings.otel_endpoint:
        return None
    return OtlpObserverFactory("loop-babysit", resource_per_agent=True)(settings.otel_endpoint)


@contextmanager
def _handling(stop: StopRequest, install: bool) -> Iterator[None]:
    """Installs the stop request's signal handlers while the block runs, when asked to."""
    saved = {name: signal.signal(name, stop.request) for name in (signal.SIGINT, signal.SIGTERM)} if install else {}
    try:
        yield
    finally:
        for name, handler in saved.items():
            signal.signal(name, handler)


def _hand_off_unclosed(ledger: Ledger, github: GitHub, work: PullRequestWork, reason: str) -> int:
    """Closes the run an error that escaped the Workflow's guard (a worktree not closed clean, a failed fetch) left unclosed as handed off, at the pull request's fingerprint; the exit code 1."""
    try:
        blocked_on = fingerprint(pull_requests.read(work, github))
    except (CalledProcessError, TimeoutExpired, OSError, ValueError):
        blocked_on = ""
    log = ledger.item(NAME, work.id)
    with log.lock(wait=True):
        log.close_run("handed_off", reason, {FINGERPRINT: blocked_on, REASON: reason})
    logger.error("%s blocked: %s", work.id, reason)
    return 1


def _report(work: PullRequestWork, result: RunResult) -> None:
    logger.info("%s %s%s", work.id, result.status, f": {result.reason}" if result.reason else "")


if __name__ == "__main__":
    raise SystemExit(main())
