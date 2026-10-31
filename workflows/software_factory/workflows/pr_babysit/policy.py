"""pr_babysit's policy as values and pure functions: whose feedback is trusted, the change fingerprint, the commit subjects and what a blocked pull request records."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from loop import RunStatus

from workflows.software_factory.events.feedback import TrustPolicy
from workflows.software_factory.events.pull_requests import PullRequestPolled

if TYPE_CHECKING:
    from workflows.software_factory.stages.address import IssueWork
    from workflows.software_factory.stages.ci import FixInput
    from workflows.software_factory.stages.conflicts import PublishInput

__all__ = [
    "ADOPTED",
    "CRASHED",
    "FINGERPRINT",
    "REASON",
    "TRUST",
    "Blocked",
    "address_subject",
    "ci_subject",
    "merge_subject",
    "fingerprint",
    "has_changed",
    "is_blocked",
    "released_for_change",
]

# Run attributes a hand-off records on the closed run, so intake can tell what a blocked pull request waits on.
FINGERPRINT = "factory.pr.fingerprint"
REASON = "factory.pr.reason"

# Set on the closed run a per-pull-request state of the earlier version was adopted as, which a person has not looked at yet.
ADOPTED = "factory.pr.adopted"

# The fingerprint the earlier version recorded for a worker that died, which no pull request ever has again.
CRASHED = "worker-crash"

# The authors whose review feedback is acted on; the acting account always is.
TRUST = TrustPolicy(
    associations=frozenset({"OWNER", "MEMBER", "COLLABORATOR"}),
    bots=frozenset(
        {
            "copilot",
            "copilot-pull-request-reviewer",
            "chatgpt-codex-connector",
            "coderabbitai",
            "claude",
            "bugbot",
        }
    ),
)


class Blocked(RuntimeError):
    """A pull request needs intervention before its maintenance can continue; infrastructure may raise it, and the definition turns it into a hand-off."""


def digest(value: object) -> str:
    """A stable key for remote work and blocked-run recovery."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def fingerprint(polled: PullRequestPolled) -> str:
    """Identifies head, base, review and CI changes, preserving the legacy component."""
    snapshot = polled.snapshot
    observed = digest(
        {
            "sha": snapshot["pr"]["head"]["sha"],
            "state": snapshot["pr"]["state"],
            "threads": snapshot["threads"],
            "other": snapshot["other"],
            "checks": [
                (c["id"], c["status"], c.get("conclusion")) for c in snapshot["checks"]
            ],
            "statuses": [(s["id"], s["state"]) for s in snapshot["statuses"]],
            "runs": [
                (r["id"], r["status"], r.get("conclusion"), r.get("run_attempt"))
                for r in snapshot["runs"]
            ],
        }
    )
    base = snapshot["pr"].get("base", {})
    return f"{observed}:{digest({key: base.get(key) for key in ('ref', 'sha')})}"


def address_subject(work: IssueWork) -> str:
    """The commit subject of the fix for one grouped review issue."""
    return f"babysit: address {work.issue.id} (PR #{work.polled.number})"


def ci_subject(work: FixInput) -> str:
    """The commit subject of the CI fix."""
    return f"babysit: fix CI (PR #{work.polled.number})"


def merge_subject(work: PublishInput) -> str:
    """The subject of the verified base-to-PR merge commit."""
    return f"babysit: merge {work.plan.base_ref} (PR #{work.polled.number})"


def is_blocked(status: RunStatus | None) -> bool:
    """Whether the latest run ended handed off and nobody has released the work since."""
    return status is not None and status.status == "handed_off" and not status.released


def has_changed(status: RunStatus | None, current: str) -> bool:
    """Whether the pull request's fingerprint is no longer the one its blocked run recorded."""
    recorded = None if status is None else status.attributes.get(FINGERPRINT)
    if recorded in (current, CRASHED):
        return False
    # Older blocked runs have no base observation; a format change must not release them.
    if isinstance(recorded, str) and ":" not in recorded:
        return recorded != current.partition(":")[0]
    return True


def released_for_change(status: RunStatus | None, current: str) -> bool:
    """Whether blocked work is released because the pull request changed since it was blocked; work adopted from the earlier version's state resumes as it was."""
    if status is None or not is_blocked(status) or status.attributes.get(ADOPTED):
        return False
    return has_changed(status, current)
