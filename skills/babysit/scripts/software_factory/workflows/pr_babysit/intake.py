"""Resolve an explicit PR and recover its existing maintenance ledger."""

from __future__ import annotations

import logging

from loop import Ledger
from loop.software_factory.events.targets import RepositoryTarget

from software_factory.src.github import GitHub
from software_factory.src.pull_requests.events.pull_requests import PullRequestWork
from software_factory.src.pull_requests.stages import pull_requests
from software_factory.src.pull_requests.stages.ci import DiagnoseInput, signal

from .connectivity import transient_account_read
from .definition import NAME
from .legacy import adopt_v1, state_path
from .policy import REASON, fingerprint, is_blocked, released_for_change
from .settings import Settings

logger = logging.getLogger("workflow.pr_babysit")

REVISION = "pull-request"


def work_of(settings: Settings, slug: str, number: int) -> PullRequestWork:
    """Resolve one configured PR and its persistent recovery worktree."""
    repository = settings.repository(slug)
    return PullRequestWork(
        RepositoryTarget(slug, repository.path, settings.worktrees_root),
        number,
        REVISION,
        repository.ignored_checks,
    )


def adopt_state(
    settings: Settings, ledger: Ledger, github: GitHub, work: PullRequestWork
) -> bool:
    """Adopt an earlier per-PR state into the ledger once."""

    def ci_signal() -> str:
        return signal(DiagnoseInput(pull_requests.read(work, github), github))

    adopted = adopt_v1(
        state_path(settings.state_dir, work.target.slug, work.number),
        ledger,
        work,
        ci_signal=ci_signal,
    )
    if adopted:
        logger.info("%s: adopted the per-PR state of the earlier version", work.id)
    return adopted


def release_if_changed(ledger: Ledger, github: GitHub, work: PullRequestWork) -> bool:
    """Release changed PRs or recovered account reads after a successful snapshot."""
    status = ledger.status(NAME, work.id)
    if status is None or not is_blocked(status):
        return False
    current = fingerprint(pull_requests.read(work, github))
    blocked_reason = status.attributes.get(REASON)
    if released_for_change(status, current):
        reason = "the pull request changed since it was blocked"
    elif isinstance(blocked_reason, str) and transient_account_read(blocked_reason):
        reason = "GitHub recovered after a temporary account read failure"
    else:
        return False
    ledger.release(NAME, work.id, reason)
    logger.info("%s: %s", work.id, reason)
    return True
