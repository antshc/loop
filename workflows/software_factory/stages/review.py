"""The review Stage: a read-only reviewer reviews the pull request on the checkout; finalization judges what it found."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loop import (
    AgentWorkspace,
    Declined,
    Evidence,
    Fail,
    Pass,
    ReadOnly,
    Stage,
    Verdict,
)

from workflows.software_factory.agents.roles.reviewer import (
    REVIEWER,
    ReviewerReply,
    ReviewerRequest,
)
from workflows.software_factory.events.review import (
    PullRequestReviewed,
    ReviewAxis,
    ReviewFailed,
    ReviewFinding,
)
from workflows.software_factory.events.specs import SpecRouted, SpecWork
from workflows.software_factory.events.tickets import Delivery
from workflows.software_factory.infrastructure.github.account import GitHub


@dataclass(frozen=True)
class ReviewInput:
    work: SpecWork
    routed: SpecRouted
    delivery: Delivery
    checkout: AgentWorkspace
    github: GitHub


def review_pull_request() -> Stage[ReviewInput, ReviewerReply, PullRequestReviewed]:
    """Read-only reviewer on the checkout, skipped unless the target enables it; a decline fails with the reason as evidence for finalization."""
    return (
        Stage(
            "review-pull-request",
            ReviewInput,
            produces=PullRequestReviewed,
            may_emit=(ReviewFailed,),
        )
        .agent(REVIEWER, request=_request)
        .gate(_reviewed)
        .contract(ReadOnly())
        .when(_enabled)
        .attributes(lambda i: {"head": i.delivery.head})
    )


def _enabled(i: ReviewInput) -> bool:
    """The target enables the review and the feature branch has a pull request to review."""
    return (
        i.routed.target.review
        and i.github.open_pull_request(i.routed.target.slug, i.routed.feature_branch)
        is not None
    )


def _request(i: ReviewInput, path: Path) -> ReviewerRequest:
    url = i.github.require_open_pull_request(
        i.routed.target.slug, i.routed.feature_branch
    ).url
    return ReviewerRequest(url, path, i.routed.initiative, i.delivery.head)


def _reviewed(
    i: ReviewInput, reply: ReviewerReply, _: Evidence
) -> Verdict[PullRequestReviewed]:
    if isinstance(reply, Declined):
        return Fail(f"the reviewer failed: {reply.reason}", ReviewFailed(reply.reason))
    return Pass(
        PullRequestReviewed(
            tuple(
                ReviewAxis(axis.name, axis.outcome, axis.note) for axis in reply.axes
            ),
            tuple(
                ReviewFinding(finding.kind, finding.summary, finding.axis)
                for finding in reply.findings
            ),
            reply.posted,
            reply.head,
        )
    )
