"""The feedback Stage: the trusted, still unanswered review feedback of a pull request, selected by a trust policy."""

from __future__ import annotations

from dataclasses import dataclass

from workflows.software_factory.events.feedback import FeedbackBatch, FeedbackHandled, TrustPolicy
from workflows.software_factory.events.pull_requests import PullRequestPolled
from loop import Evidence, Hold, History, Pass, Stage, Verdict
from loop.shared.github.account import GitHub
from loop.shared.github.pull_requests.babysit import ci_failed, unhandled_items


@dataclass(frozen=True)
class SelectInput:
    polled: PullRequestPolled
    handled: History[FeedbackHandled]
    github: GitHub


def select(*, trust: TrustPolicy) -> Stage[SelectInput, FeedbackBatch, FeedbackBatch]:
    """The pull request's pending feedback from authors `trust` accepts, minus what earlier batches replied to; holds when there is none."""
    return (
        Stage("select-feedback", SelectInput, produces=FeedbackBatch)
        .action(lambda i: _batch(i, trust))
        .gate(_selected)
    )


def _batch(i: SelectInput, trust: TrustPolicy) -> FeedbackBatch:
    snapshot = i.polled.snapshot
    handled = {key for event in i.handled for key in event.keys}
    items = unhandled_items(snapshot, i.github.login(), trust, handled, frozenset(i.polled.bots))
    return FeedbackBatch(i.polled.head, ci_failed(snapshot), tuple(items))


def _selected(_: SelectInput, batch: FeedbackBatch, __: Evidence) -> Verdict[FeedbackBatch]:
    return Pass(batch) if batch.items else Hold("no pending feedback")
