"""`ignored_checks` drops matching failed checks, and the workflow runs failing only through them, from the CI state."""

from __future__ import annotations

from typing import Any

from software_factory.src.pull_requests.infrastructure.github.pull_requests.babysit import (
    ci_state,
    ignore_checks,
)
from software_factory.src.pull_requests.stages.pull_requests import _ready

SECRETS = "compliance-dispatcher / compliance / secrets scanner / secret-scanner"
TRIVY = "compliance-dispatcher / compliance / trivy"


def _check(id_: int, name: str, conclusion: str, suite: int = 7) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "check_suite": {"id": suite},
    }


def _snapshot(*checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "pr": {
            "draft": False,
            "mergeable": True,
            "mergeable_state": "unstable",
            "requested_reviewers": [],
        },
        "review": {"reviewDecision": "APPROVED"},
        "checks": list(checks),
        "statuses": [{"id": 1, "context": "jenkins/pr-head", "state": "success"}],
        "runs": [
            {
                "id": 9,
                "name": "CI compliance with Slack Notifications Dispatcher",
                "workflow_id": 3,
                "event": "pull_request",
                "status": "completed",
                "conclusion": "failure",
                "check_suite_id": 7,
            }
        ],
    }


def test_run_failing_only_through_an_ignored_job_is_green_and_ready() -> None:
    snapshot = _snapshot(_check(1, SECRETS, "failure"), _check(2, TRIVY, "success"))

    filtered = ignore_checks(snapshot, ("*secret-scanner",))

    assert ci_state(filtered)["green"]
    assert filtered["ignored"] == [
        "CI compliance with Slack Notifications Dispatcher",
        SECRETS,
    ]
    assert _ready(filtered)
    assert not _ready(snapshot)


def test_run_with_another_failed_job_still_fails() -> None:
    snapshot = _snapshot(_check(1, SECRETS, "failure"), _check(2, TRIVY, "failure"))

    state = ci_state(ignore_checks(snapshot, ("*secret-scanner",)))

    assert state["failed"]
    assert [run["id"] for run in state["runs"]] == [9]
