"""The finalize Stage: mark the draft pull request ready only when no readiness blocker exists, and say so on the Spec."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from software_factory.events.chore import ChoreDiscarded, ChoreRefined
from software_factory.events.pull_requests import DraftPullRequestPublished, Finalized
from software_factory.events.review import PullRequestReviewed, ReviewFailed
from software_factory.events.specs import SpecRouted, SpecWork
from software_factory.events.tickets import Delivery
from software_factory.runtime import Stage
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.issues.issue import IssueState
from software_factory.shared.github.pull_requests import PublishedPullRequest

REQUIRED_AXES = frozenset({"bugs", "quality", "smells", "initiative"})
# Findings of these kinds keep the pull request a draft; the others are only reported.
BLOCKING_FINDING_KINDS = frozenset({"bug", "initiative"})


@dataclass(frozen=True)
class FinalizeInput:
    work: SpecWork
    routed: SpecRouted
    delivery: Delivery
    published: DraftPullRequestPublished
    chore: ChoreRefined | ChoreDiscarded | None
    review: PullRequestReviewed | ReviewFailed | None
    tracker: Issues
    git: GitClient
    github: GitHub


@dataclass(frozen=True)
class _Assessment:
    """Everything the readiness decision rests on, read once."""

    blockers: tuple[str, ...]
    pull_request: PublishedPullRequest | None
    summary: str

    @property
    def ready(self) -> bool:
        return not self.blockers


def finalize(
    *,
    label: str | None = None,
    source: str = "implement_spec",
    axes: Collection[str] = REQUIRED_AXES,
    blocking: Collection[str] = BLOCKING_FINDING_KINDS,
) -> Stage[FinalizeInput, Finalized, Finalized]:
    """Marks the pull request ready only when no readiness blocker exists, then comments the summary on the Spec; otherwise keeps it draft and labels the Spec `label` when given. Never merges or closes the Spec."""
    def assess(i: FinalizeInput) -> _Assessment:
        return _assess(i, source, frozenset(axes), frozenset(blocking))

    def act(i: FinalizeInput) -> Finalized:
        assessment = assess(i)
        return _recognized(i, assessment, label) or _apply(i, assessment, label)

    return Stage("finalize", FinalizeInput, produces=Finalized).effect(
        act, reconcile=lambda i: _recognized(i, assess(i), label),
    )


def _readiness_blockers(
    i: FinalizeInput, spec: SpecGraphSnapshot, current_head: str, pull_request: PublishedPullRequest | None,
    axes: frozenset[str], blocking: frozenset[str],
) -> tuple[str, ...]:
    """Why the pull request must stay a draft; empty when every gate passes."""
    delivery = i.delivery
    blockers: list[str] = []
    if not spec.complete:
        blockers.append("the Spec could not be read completely")
    open_tickets = [ticket.number for ticket in spec.deliverable_tickets if ticket.state is IssueState.OPEN]
    if open_tickets:
        blockers.append("not every Ticket was delivered; open Tickets: " + ", ".join(f"#{number}" for number in open_tickets))
    if spec.held_tickets:
        blockers.append(
            "Tickets held (blocked, unread dependencies, or waiting for a human): " + ", ".join(f"#{number}" for number in spec.held_tickets)
        )
    blockers += _review_blockers(i.review, delivery.head, axes, blocking)
    if current_head != delivery.head:
        blockers.append(f"the pull request head moved to {current_head} after the review started")
    if pull_request is None:
        blockers.append("there is no open pull request to mark ready")
    return tuple(blockers)


def _review_blockers(
    review: PullRequestReviewed | ReviewFailed | None, head: str, axes: frozenset[str], blocking: frozenset[str],
) -> list[str]:
    if review is None:
        return ["the review did not run"]
    if isinstance(review, ReviewFailed):
        return [f"the review failed: {review.reason}"]
    blockers: list[str] = []
    if {axis.name for axis in review.axes} != axes or any(axis.outcome != "completed" for axis in review.axes):
        blockers.append("a review axis did not complete")
    if review.head != head:
        blockers.append(f"the review covered {review.head or 'an unknown head'}, not {head}")
    unresolved = [finding for finding in review.findings if finding.kind in blocking]
    if unresolved:
        blockers.append(f"{len(unresolved)} unresolved bug/initiative finding(s)")
    return blockers


def _assess(i: FinalizeInput, source: str, axes: frozenset[str], blocking: frozenset[str]) -> _Assessment:
    routed = i.routed
    i.git.fetch(routed.target.checkout)
    current_head = i.git.ref_sha(routed.target.checkout, f"origin/{routed.feature_branch}")
    spec = i.tracker.spec(i.work.number)
    pull_request = i.github.pull_requests(routed.target.slug).find_open(routed.feature_branch)
    blockers = _readiness_blockers(i, spec, current_head, pull_request, axes, blocking)
    url = pull_request.url if pull_request is not None else i.published.url
    return _Assessment(blockers, pull_request, _summary(i, source, url, blockers))


def _recognized(i: FinalizeInput, assessment: _Assessment, label: str | None) -> Finalized | None:
    """The finalization a crashed attempt completed: the summary is on the Spec, the pull request is ready when it should be, and the Spec carries the label when blocked."""
    if not _commented(i.tracker, i.work.number, assessment.summary):
        return None
    if assessment.ready and assessment.pull_request is not None and assessment.pull_request.draft:
        return None
    if not assessment.ready and label is not None and label not in i.tracker.issue(i.work.number).labels:
        return None
    return _finalized(assessment)


def _apply(i: FinalizeInput, assessment: _Assessment, label: str | None) -> Finalized:
    pull_request = assessment.pull_request
    if assessment.ready and pull_request is not None and pull_request.draft:
        i.github.pull_requests(i.routed.target.slug).mark_ready(pull_request.url)
    i.tracker.comment(i.work.number, "review-summary", assessment.summary)
    # The label is written last, so it marks a finished hand-off.
    if not assessment.ready and label is not None:
        i.tracker.add_label(i.work.number, label)
    return _finalized(assessment)


def _finalized(assessment: _Assessment) -> Finalized:
    pull_request = assessment.pull_request
    return Finalized(assessment.ready, None if pull_request is None else pull_request.url, assessment.blockers)


def _summary(i: FinalizeInput, source: str, url: str | None, blockers: tuple[str, ...]) -> str:
    state = "**left as a draft**" if blockers else "**ready for review**"
    lines = [f"{source}: pull request {url or '(none)'} is {state}", "", "Commits:", *(f"- {commit}" for commit in i.delivery.commits)]
    lines += _chore_lines(i.chore)
    lines += _review_lines(i.review)
    if blockers:
        lines += ["", "Kept as a draft because:", *(f"- {blocker}" for blocker in blockers)]
    return "\n".join(lines)


def _chore_lines(chore: ChoreRefined | ChoreDiscarded | None) -> list[str]:
    if chore is None:
        return []
    if isinstance(chore, ChoreDiscarded):
        return [f"Chore: discarded ({chore.reason})", *(f"- out of scope: {item}" for item in chore.out_of_scope)]
    status = "committed" if chore.committed else "nothing to change"
    return [f"Chore: {status}" + (f" ({chore.summary})" if chore.summary else ""), *(f"- out of scope: {item}" for item in chore.out_of_scope)]


def _review_lines(review: PullRequestReviewed | ReviewFailed | None) -> list[str]:
    if review is None:
        return ["Review: not completed (it did not run)"]
    if isinstance(review, ReviewFailed):
        return [f"Review: not completed ({review.reason})"]
    return ["Review: " + ", ".join(f"{axis.name} {axis.outcome}" for axis in review.axes), *(f"- {finding.kind}: {finding.summary}" for finding in review.findings)]


def _commented(tracker: Issues, number: int, body: str) -> bool:
    """Whether the issue already carries a Comment that starts with `body` (the tracker appends its marker after it)."""
    return any(comment.body.startswith(body) for comment in tracker.issue(number).comments)
