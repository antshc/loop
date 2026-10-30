"""The Events of reviewing a pull request."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.runtime import Event


@dataclass(frozen=True)
class ReviewAxis:
    """The outcome of one review axis, such as `bugs` or `quality`."""

    name: str
    outcome: str
    note: str = ""


@dataclass(frozen=True)
class ReviewFinding:
    """One finding of a review axis."""

    kind: str
    summary: str
    axis: str = ""


@dataclass(frozen=True)
class PullRequestReviewed(Event):
    """The reviewer's answer: per-axis outcomes, findings, the references of the comments it posted and the head it reviewed."""

    axes: tuple[ReviewAxis, ...]
    findings: tuple[ReviewFinding, ...]
    posted: tuple[str, ...]
    head: str


@dataclass(frozen=True)
class ReviewFailed(Event):
    """The reviewer could not review the pull request; kept as evidence for finalization."""

    reason: str
