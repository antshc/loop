"""The reviewer's request and replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...contract import Declined, Message


@dataclass(frozen=True)
class ReviewerRequest(Message):
    """One pull request to review read-only on the checkout at `source_root`."""

    pull_request_url: str
    source_root: Path
    initiative: str
    head_sha: str


@dataclass(frozen=True)
class ReviewedAxis(Message):
    """The outcome of one review axis, such as `bugs` or `quality`."""

    name: str
    outcome: str
    note: str = ""


@dataclass(frozen=True)
class ReviewedFinding(Message):
    """One finding of a review axis."""

    kind: str
    summary: str
    axis: str = ""


@dataclass(frozen=True)
class Reviewed(Message):
    """Per-axis outcomes, findings, the references of the comments the agent posted, and the head it reviewed."""

    axes: tuple[ReviewedAxis, ...] = ()
    findings: tuple[ReviewedFinding, ...] = ()
    posted: tuple[str, ...] = ()
    head: str = ""


type ReviewerReply = Reviewed | Declined
