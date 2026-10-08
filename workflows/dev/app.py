"""Spec orchestration: prepare, deliver, publish, and announce each open Spec."""

from __future__ import annotations

import logging
from enum import Enum, auto
from pathlib import Path

from loop import Branch, BranchService, Cancelled, LoopError, WorktreeSandbox
from workflows.platforms.work_tracking import HITL_LABEL, Spec

from .delivery import deliver_tickets, open_sandbox
from .deps import DevDeps
from .planning import SpecRun, prepare_run

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


def publish(branches: BranchService, run: SpecRun, pusher: Path) -> str | None:
    """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

    Returns the PR URL, or None when there was nothing to publish.
    """
    if not branches.push(Branch(pusher, run.feature_branch), Branch(pusher, run.spec.base_branch)):
        return None
    return run.pull_requests.publish_draft(run.spec, run.feature_branch)


def _base_branch_exists(run: SpecRun, deps: DevDeps) -> bool:
    """True when the base branch is on the target's origin; otherwise hands the Spec to a human."""
    if deps.git.branches.can_prepare(Branch(run.checkout, run.spec.base_branch)):
        return True
    deps.tracker.hitl(run.spec.number, f"dev: target branch {run.spec.base_branch!r} does not exist on {run.spec.target}")
    return False


def _prepare(run: SpecRun, deps: DevDeps) -> WorktreeSandbox | None:
    """Publishes earlier runs' commits; returns a sandbox only when there are Tickets to deliver."""
    deps.git.branches.fetch(run.checkout)
    if not run.tickets:
        publish(deps.git.branches, run, run.checkout)
        return None
    if not _base_branch_exists(run, deps):
        return None
    # Worktree creation force-resets the local feature branch, so publish earlier runs' commits first.
    publish(deps.git.branches, run, run.checkout)
    return open_sandbox(run, deps)


def _deliver_in_sandbox(run: SpecRun, sandbox: WorktreeSandbox, deps: DevDeps) -> Outcome:
    """Delivers and publishes inside `sandbox`, always closing it; a cancelled run keeps only uncommitted work."""
    kept_on_cancel = False
    try:
        delivered = deliver_tickets(run, sandbox, deps)
        pull_request_url = publish(deps.git.branches, run, sandbox.worktree.path)
        if delivered and pull_request_url is not None:
            deps.tracker.announce_delivered(run.spec.number, pull_request_url)
        return Outcome.SUCCESS if delivered else Outcome.FAILED
    except Cancelled:
        kept_on_cancel = deps.git.worktrees.has_changes(sandbox.worktree)
        _report_cancelled(run.spec, sandbox.worktree.path if kept_on_cancel else None)
        return Outcome.SKIPPED
    finally:
        sandbox.close(keep_worktree=kept_on_cancel)


def _process_spec(spec: Spec, deps: DevDeps) -> Outcome:
    """Prepare, deliver, and publish one Spec."""
    run = prepare_run(
        spec,
        harness_root=deps.harness_root,
        harness_slug=deps.harness_slug,
        tracker=deps.tracker,
        github_factory=deps.github_factory,
        git=deps.git,
    )
    if run is None:
        return Outcome.SKIPPED

    try:
        sandbox = _prepare(run, deps)
    except Cancelled as exception:
        _report_cancelled(spec, exception.worktree)
        return Outcome.SKIPPED
    except LoopError as exception:
        deps.tracker.comment(run.spec.number, f"dev: {exception}")
        return Outcome.FAILED

    if sandbox is None:
        return Outcome.SKIPPED
    return _deliver_in_sandbox(run, sandbox, deps)


def process_specs(deps: DevDeps) -> int:
    """Processes every Spec not waiting for a human; returns the process exit code."""
    failed = False
    for spec in deps.tracker.specs():
        if HITL_LABEL in spec.labels:
            continue
        failed = (_process_spec(spec, deps) is Outcome.FAILED) or failed
    return 1 if failed else 0
