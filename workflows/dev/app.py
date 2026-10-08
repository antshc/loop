"""Spec orchestration: prepare, deliver, publish, and announce each open Spec."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from loop import AgentRunner, Branch, BranchService, Cancelled, LoopError
from workflows.platforms.work_tracking import (
    HITL_LABEL,
    Repository,
    RepositoryPool,
    Spec,
    Ticket,
    TicketsTracker,
)

from .delivery import deliver_tickets, open_runner
from .deps import DevDeps

logger = logging.getLogger("workflow.dev")


@dataclass(frozen=True)
class SpecRun:
    spec: Spec
    tickets: tuple[Ticket, ...]
    repository: Repository


def prepare_run(
    spec: Spec,
    *,
    repository_pool: RepositoryPool,
    tracker: TicketsTracker,
) -> SpecRun | None:
    """The SpecRun for `spec`, or None after blocking the Spec when its target cannot be resolved."""
    # Tickets live on the harness tracker, even when the Spec targets another repo.
    tickets = tracker.get_tickets(spec)

    target = spec.target
    base_branch = spec.base_branch
    if target is None or base_branch is None:
        tracker.block_spec(spec.number, tickets, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return None

    repository = repository_pool.get(target)
    if repository is None:
        tracker.block_spec(spec.number, tickets, f"dev: repo:target:{target} is not configured in the RepositoryPool")
        return None

    return SpecRun(
        spec=spec,
        tickets=tickets,
        repository=repository,
    )


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


def _report_cancelled(spec: Spec, worktree: Path | None) -> None:
    if worktree is not None:
        logger.warning("spec #%s run cancelled; worktree kept at %s", spec.number, worktree)
    else:
        logger.info("spec #%s run cancelled", spec.number)


def publish(branches: BranchService, run: SpecRun, pusher: Path) -> str | None:
    """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

    Returns the PR URL, or None when there was nothing to publish.
    """
    if not branches.push(Branch(pusher, run.spec.feature_branch), Branch(pusher, run.spec.base_branch)):
        return None
    return run.repository.pull_requests.publish_draft(run.spec, run.spec.feature_branch)


def _base_branch_exists(run: SpecRun, deps: DevDeps) -> bool:
    """True when the base branch is on the target's origin; otherwise hands the Spec to a human."""
    if deps.git.branches.can_prepare(Branch(run.repository.path, run.spec.base_branch)):
        return True
    deps.tracker.hitl(run.spec.number, f"dev: target branch {run.spec.base_branch!r} does not exist on {run.spec.target}")
    return False


def _prepare(run: SpecRun, deps: DevDeps) -> AgentRunner | None:
    """Publishes earlier runs' commits; returns a runner only when there are Tickets to deliver."""
    deps.git.branches.fetch(run.repository.path)
    if not run.tickets:
        publish(deps.git.branches, run, run.repository.path)
        return None
    if not _base_branch_exists(run, deps):
        return None
    # Worktree creation force-resets the local feature branch, so publish earlier runs' commits first.
    publish(deps.git.branches, run, run.repository.path)
    return open_runner(run, deps)


def _deliver_in_worktree(run: SpecRun, runner: AgentRunner, deps: DevDeps) -> Outcome:
    """Delivers and publishes on `runner`'s worktree, always closing it; a cancelled run keeps only uncommitted work."""
    kept_on_cancel = False
    try:
        delivered = deliver_tickets(run, runner, deps)
        pull_request_url = publish(deps.git.branches, run, runner.worktree.path)
        if delivered and pull_request_url is not None:
            deps.tracker.announce_delivered(run.spec.number, pull_request_url)
        return Outcome.SUCCESS if delivered else Outcome.FAILED
    except Cancelled:
        kept_on_cancel = runner.lifecycle.has_changes()
        _report_cancelled(run.spec, runner.worktree.path if kept_on_cancel else None)
        return Outcome.SKIPPED
    finally:
        runner.exit(keep_worktree=kept_on_cancel)


def _process_spec(spec: Spec, deps: DevDeps) -> Outcome:
    """Prepare, deliver, and publish one Spec."""
    run = prepare_run(
        spec,
        repository_pool=deps.repository_pool,
        tracker=deps.tracker,
    )
    if run is None:
        return Outcome.SKIPPED

    try:
        runner = _prepare(run, deps)
    except Cancelled as exception:
        _report_cancelled(spec, exception.worktree)
        return Outcome.SKIPPED
    except LoopError as exception:
        deps.tracker.comment(run.spec.number, f"dev: {exception}")
        return Outcome.FAILED

    if runner is None:
        return Outcome.SKIPPED
    return _deliver_in_worktree(run, runner, deps)


def process_specs(deps: DevDeps) -> int:
    """Processes every Spec not waiting for a human; returns the process exit code."""
    failed = False
    for spec in deps.tracker.specs():
        if HITL_LABEL in spec.labels:
            continue
        failed = (_process_spec(spec, deps) is Outcome.FAILED) or failed
    return 1 if failed else 0
