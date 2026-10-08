"""Spec orchestration: prepare, deliver, publish, and announce each open Spec."""

from __future__ import annotations

import logging
from enum import Enum, auto
from pathlib import Path

from loop import AgentRunner, Branch, BranchService, Cancelled, LoopError
from workflows.platforms.work_tracking import Repository, Spec

from .delivery import deliver_tickets, open_runner
from .deps import DevDeps

logger = logging.getLogger("workflow.dev")


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


def _report_cancelled(spec: Spec, worktree: Path | None) -> None:
    if worktree is not None:
        logger.warning("spec #%s run cancelled; worktree kept at %s", spec.number, worktree)
    else:
        logger.info("spec #%s run cancelled", spec.number)


def publish(branches: BranchService, spec: Spec, repository: Repository, pusher: Path) -> str | None:
    """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

    Returns the PR URL, or None when there was nothing to publish.
    """
    if not branches.push(Branch(pusher, spec.feature_branch), Branch(pusher, spec.base_branch)):
        return None
    return repository.pull_requests.publish_draft(spec, spec.feature_branch)


def _base_branch_exists(spec: Spec, repository: Repository, deps: DevDeps) -> bool:
    """True when the base branch is on the target's origin; otherwise hands the Spec to a human."""
    if deps.git.branches.can_prepare(Branch(repository.path, spec.base_branch)):
        return True
    deps.tracker.hitl(spec.number, f"dev: target branch {spec.base_branch!r} does not exist on {spec.target}")
    return False


def _prepare(spec: Spec, repository: Repository, deps: DevDeps) -> AgentRunner | None:
    """Publishes earlier runs' commits; returns a runner only when there are Tickets to deliver."""
    deps.git.branches.fetch(repository.path)
    if not spec.has_work:
        publish(deps.git.branches, spec, repository, repository.path)
        return None
    if not _base_branch_exists(spec, repository, deps):
        return None
    # Worktree creation force-resets the local feature branch, so publish earlier runs' commits first.
    publish(deps.git.branches, spec, repository, repository.path)
    return open_runner(spec, repository, deps)


def _deliver_in_worktree(spec: Spec, repository: Repository, runner: AgentRunner, deps: DevDeps) -> Outcome:
    """Delivers and publishes on `runner`'s worktree, always closing it; a cancelled run keeps only uncommitted work."""
    kept_on_cancel = False
    try:
        delivered = deliver_tickets(spec, runner, deps)
        pull_request_url = publish(deps.git.branches, spec, repository, runner.worktree.path)
        if delivered and pull_request_url is not None:
            deps.tracker.announce_delivered(spec.number, pull_request_url)
        return Outcome.SUCCESS if delivered else Outcome.FAILED
    except Cancelled:
        kept_on_cancel = runner.lifecycle.has_changes()
        _report_cancelled(spec, runner.worktree.path if kept_on_cancel else None)
        return Outcome.SKIPPED
    finally:
        runner.exit(keep_worktree=kept_on_cancel)


def _process_spec(spec: Spec, deps: DevDeps) -> Outcome:
    """Prepare, deliver, and publish one Spec."""
    if spec.target is None or spec.base_branch is None:
        deps.tracker.block_spec(spec, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return Outcome.SKIPPED

    repository = deps.repository_pool.get(spec.target)
    if repository is None:
        deps.tracker.block_spec(spec, f"dev: repo:target:{spec.target} is not configured in the RepositoryPool")
        return Outcome.SKIPPED

    try:
        runner = _prepare(spec, repository, deps)
    except Cancelled as exception:
        _report_cancelled(spec, exception.worktree)
        return Outcome.SKIPPED
    except LoopError as exception:
        deps.tracker.comment(spec.number, f"dev: {exception}")
        return Outcome.FAILED

    if runner is None:
        return Outcome.SKIPPED
    return _deliver_in_worktree(spec, repository, runner, deps)


def process_specs(deps: DevDeps) -> int:
    """Processes every Spec not waiting for a human; returns the process exit code."""
    failed = False
    for spec in deps.tracker.specs():
        if spec.awaiting_human:
            continue
        failed = (_process_spec(spec, deps) is Outcome.FAILED) or failed
    return 1 if failed else 0
