"""The Copilot review Stage: ask Copilot to review a pull request head once."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.events.pull_requests import CopilotDue, CopilotRequested, PullRequestPolled
from software_factory.runtime import Evidence, History, Pass, Stage, Verdict
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.pull_requests.babysit import is_copilot


@dataclass(frozen=True)
class RequestInput:
    due: CopilotDue
    polled: PullRequestPolled
    requested: History[CopilotRequested]
    github: GitHub

    @property
    def head(self) -> str:
        """The pull request head the review is wanted for."""
        return self.due.head


def request_review() -> Stage[RequestInput, CopilotRequested, CopilotRequested]:
    """Asks Copilot to review the head, once per head across runs; a head already requested, or a review already pending on the pull request, is recorded again without a second request, and a refusal from GitHub is recorded as unavailable."""
    return (
        Stage("request-copilot-review", RequestInput, produces=CopilotRequested)
        .effect(lambda i: _requested(i) or _request(i), reconcile=_requested)
        .gate(_recorded)
        .once(per=lambda i: i.head)
    )


def _recorded(_: RequestInput, requested: CopilotRequested, __: Evidence) -> Verdict[CopilotRequested]:
    return Pass(requested)


def _requested(i: RequestInput) -> CopilotRequested | None:
    """The request an earlier run recorded for the head, or the review already pending on the pull request."""
    earlier = next((event for event in i.requested if event.head == i.head), None)
    if earlier is not None:
        return earlier
    pull = i.github.pull_requests(i.polled.target.slug).pull_request(i.polled.number)
    return CopilotRequested(i.head, "requested") if any(is_copilot(login) for login in pull.requested_reviewers) else None


def _request(i: RequestInput) -> CopilotRequested:
    accepted = i.github.pull_requests(i.polled.target.slug).try_request_copilot(i.polled.number)
    return CopilotRequested(i.head, "requested" if accepted else "unavailable")
