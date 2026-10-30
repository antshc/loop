"""The Copilot review Stage: one request per head across runs, reconciled against the reviews already requested, and a refusal recorded without retrying."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from software_factory.events.feedback import FeedbackBatch, FeedbackHandled
from software_factory.events.pull_requests import (
    CiFailing, CopilotDue, CopilotRequested, NextAction, PullRequestClosed, ReadinessCheck, Unsupported,
)
from software_factory.runtime import Flow, Workflow, stop
from software_factory.stages import copilot, pull_requests
from software_factory.tests.stages.pr_support import NUMBER, TRUST
from software_factory.tests.stages.watch_support import WatchRig, produces, watch

COPILOT_BOT = "copilot-pull-request-reviewer[bot]"


def request_copilot() -> Workflow[Any]:
    """Snapshot, decide with Copilot review enabled, and request the review for a head it is due on, once per head."""
    cases = {variant: stop.completed() for variant in (PullRequestClosed, Unsupported, FeedbackBatch, CiFailing, ReadinessCheck)}
    due = Flow("copilot").stage(copilot.request_review().once(per=lambda i: i.head))
    return (
        watch(pull_requests.snapshot(), pull_requests.decide_next(trust=TRUST, author=None, copilot=lambda _: True))
        .dispatch(NextAction, {**cases, CopilotDue: due})
        .stage(produces(FeedbackHandled((), "h")))
    )


def test_a_copilot_review_is_requested_once_for_a_head_and_never_again_in_later_runs(tmp_path: Path) -> None:
    """Given a head Copilot was not asked to review, when the pull request is watched in two runs, then exactly one request is made and it is recorded as requested."""
    rig = WatchRig(tmp_path)

    first, second = rig.run_watch(request_copilot()), rig.run_watch(request_copilot())

    assert (first.status, second.status) == ("completed", "completed")
    assert rig.host.requested == [NUMBER]
    assert rig.recorded(CopilotRequested) == (CopilotRequested(rig.head, "requested"),)


def test_a_new_head_gets_exactly_one_request_of_its_own(tmp_path: Path) -> None:
    """Given a review already requested for one head, when the pull request has a new head, then exactly one more request is made and the next run makes none."""
    rig = WatchRig(tmp_path)
    rig.run_watch(request_copilot())
    rig.pull.requested_reviewers = []
    rig.pull.head_sha = "b" * 40

    rig.run_watch(request_copilot())
    rig.run_watch(request_copilot())

    assert rig.host.requested == [NUMBER, NUMBER]
    assert [event.head for event in rig.recorded(CopilotRequested)] == [rig.head, "b" * 40]


def test_a_review_request_already_pending_on_the_pull_request_is_recorded_without_a_second_request(tmp_path: Path) -> None:
    """Given a Copilot review already requested on the pull request, when the Stage runs, then it reconciles with it, makes no request and records the head as requested."""
    rig = WatchRig(tmp_path)
    rig.pull.requested_reviewers = [COPILOT_BOT]

    rig.run_watch(request_copilot())

    assert rig.host.requested == []
    assert rig.recorded(CopilotRequested) == (CopilotRequested(rig.head, "requested"),)


def test_a_refused_request_is_recorded_as_unavailable_and_not_retried(tmp_path: Path) -> None:
    """Given a repository where GitHub refuses Copilot review requests, when the pull request is watched twice, then the head is recorded unavailable and no further request is made."""
    rig = WatchRig(tmp_path)
    rig.gh.fail_on("pr", "edit")

    first, second = rig.run_watch(request_copilot()), rig.run_watch(request_copilot())

    assert (first.status, second.status) == ("completed", "completed")
    assert rig.recorded(CopilotRequested) == (CopilotRequested(rig.head, "unavailable"),)
    assert rig.host.requested == []


def test_reconcile_recognizes_an_earlier_request_for_the_head_but_not_for_another(tmp_path: Path) -> None:
    """Given a request recorded for one head, when the Stage reconciles, then it finds the request for that head and nothing for another."""
    rig = WatchRig(tmp_path)
    stage = copilot.request_review()
    earlier = (CopilotRequested("h1", "unavailable"),)

    found = stage.executor.reconcile(copilot.RequestInput(CopilotDue("h1"), rig.polled(), earlier, rig.github))
    missing = stage.executor.reconcile(copilot.RequestInput(CopilotDue("h2"), rig.polled(), earlier, rig.github))

    assert found == CopilotRequested("h1", "unavailable") and missing is None
