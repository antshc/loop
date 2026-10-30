"""The address_once command: address the trusted review feedback of one pull request once, through the factory runtime."""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired

from loop import Agent, WorktreeError

from software_factory.events.pull_requests import PullRequestWork
from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Ledger, LogSpans, RunResult, Services, Spans, WorktreeGit, run
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.repo import GhCli
from software_factory.shared.process import CommandRunner, git_command
from software_factory.stages.checks import as_verify

from . import NAME, address_once
from .settings import Settings

logger = logging.getLogger("workflow.address_once")

_PULL_REQUEST = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)/?")

_GIT_TIMEOUT_S = 120

_INTERRUPTED = 130

# What a pull request work item is about: itself, which does not change while it is addressed.
_REVISION = "pull-request"


@dataclass(frozen=True)
class Runtime:
    """How the run is recorded and observed; a test passes its own among the services."""

    ledger: Ledger
    spans: Spans


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="address_once", description="Address the trusted review feedback of one PR once")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pr", required=True, help="https://github.com/owner/repo/pull/123")
    return parser


def build_services() -> Services:
    """The services of the definition, all real."""
    run_process = CommandRunner(timeout_s=_GIT_TIMEOUT_S)
    return (
        Services(GitClient(run_process), GitHub(GhCli(run_process)), AgentLauncher(Agent, None))
        .bind(WorktreeGit, WorktreeGit(git_command))
    )


def main(argv: Sequence[str] | None = None, *, services: Services | None = None) -> int:
    """Addresses one pull request once and returns the run's exit code; `services` replaces every external dependency."""
    parser = _parser()
    args = parser.parse_args(argv)
    match = _PULL_REQUEST.fullmatch(args.pr)
    if not match:
        parser.error("--pr must be a GitHub PR URL")
    logging.basicConfig(level=logging.INFO)
    try:
        return _address(Settings.load(args.config), services or build_services(), match[1], int(match[2]))
    except (WorktreeError, CalledProcessError, TimeoutExpired, OSError, ValueError) as exception:
        logger.error("address_once stopped: %s", exception)
        return 1
    except KeyboardInterrupt:
        logger.warning("address_once interrupted")
        return _INTERRUPTED


def _address(settings: Settings, services: Services, slug: str, number: int) -> int:
    repository = settings.repository(slug)
    runtime = services.get(Runtime) if Runtime in services.types() else Runtime(Ledger(settings.ledger_root), LogSpans())
    work = PullRequestWork(RepositoryTarget(slug, repository.path, settings.state_dir / "worktrees"), number, _REVISION)
    definition = address_once(as_verify(repository.test, timeout_s=settings.timeout, run=CommandRunner()))
    try:
        result = run(definition, work, services=services, ledger=runtime.ledger, spans=runtime.spans)
    except WorktreeError as exception:
        return _hand_off_unclosed(runtime.ledger, work, str(exception))
    _report(work, result)
    return result.exit_code


def _hand_off_unclosed(ledger: Ledger, work: PullRequestWork, reason: str) -> int:
    """Closes the run a worktree that could not be closed clean left unclosed as handed off with the reason; the exit code 1."""
    log = ledger.item(NAME, work.id)
    with log.lock(wait=True):
        log.close_run("handed_off", reason, {})
    logger.error("%s blocked: %s", work.id, reason)
    return 1


def _report(work: PullRequestWork, result: RunResult) -> None:
    logger.info("%s %s%s", work.id, result.status, f": {result.reason}" if result.reason else "")


if __name__ == "__main__":
    raise SystemExit(main())
