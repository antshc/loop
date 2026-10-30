"""The readiness Stage: readiness is reported once per head and readiness, so a poll that finds nothing new reports nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.software_factory.events.feedback import FeedbackBatch, FeedbackHandled
from workflows.software_factory.events.pull_requests import (
    CiFailing, CopilotDue, CopilotRequested, NextAction, PullRequestClosed, ReadinessCheck, ReadinessReported, Unsupported,
)
from loop import Flow, Workflow, stop
from workflows.software_factory.stages import pull_requests, readiness
from workflows.tests.software_factory.stages.pr_support import TRUST
from workflows.tests.software_factory.stages.watch_support import WatchRig, produces, watch


def report_readiness() -> Workflow[Any]:
    """Snapshot, decide, and report the readiness of a pull request with nothing else to do."""
    cases = {variant: stop.completed() for variant in (PullRequestClosed, Unsupported, FeedbackBatch, CiFailing, CopilotDue)}
    return (
        watch(pull_requests.snapshot(), pull_requests.decide_next(trust=TRUST, author=None, copilot=lambda _: False))
        .dispatch(NextAction, {**cases, ReadinessCheck: Flow("ready").stage(readiness.report())})
        .stage(produces(FeedbackHandled((), "h")))
        .stage(produces(CopilotRequested("h", "requested")))
    )


def reports(rig: WatchRig) -> list[tuple[str, bool]]:
    """The readiness reports the pull request's runs recorded, in order."""
    return [(event.head, event.ready) for event in rig.recorded(ReadinessReported)]


def test_readiness_is_reported_once_and_a_later_poll_with_the_same_readiness_reports_nothing(tmp_path: Path) -> None:
    """Given a ready pull request, when it is polled again with nothing changed, then readiness is reported once and the second poll exports no report span."""
    rig = WatchRig(tmp_path)

    rig.run_watch(report_readiness())
    rig.run_watch(report_readiness())

    assert reports(rig) == [(rig.head, True)]
    reported = rig.spans.attempts("report-readiness")
    assert len(reported) == 1 and reported[0].attributes["factory.head"] == rig.head and reported[0].attributes["factory.ready"] is True


def test_a_change_of_readiness_for_the_same_head_is_reported_once(tmp_path: Path) -> None:
    """Given a ready pull request whose CI then goes pending, when it is polled, then the change is reported once and a repeat of it is not."""
    rig = WatchRig(tmp_path)
    rig.run_watch(report_readiness())
    rig.pend_ci()

    rig.run_watch(report_readiness())
    rig.run_watch(report_readiness())

    assert reports(rig) == [(rig.head, True), (rig.head, False)]


def test_the_same_readiness_on_a_new_head_is_reported_again(tmp_path: Path) -> None:
    """Given a ready pull request that gets a new head and is ready again, when it is polled, then readiness is reported for the new head."""
    rig = WatchRig(tmp_path)
    rig.run_watch(report_readiness())
    rig.pull.head_sha = "b" * 40

    rig.run_watch(report_readiness())

    assert reports(rig) == [(rig.head, True), ("b" * 40, True)]
