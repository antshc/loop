"""A pull request as an immutable snapshot: its own state, review threads, reviews, conversation Comments, and the check results tied to the commits they ran against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..identity import IssueIdentity, IssueRef
from ..issues.issue import Comment


# A fresh field per class: the GitHub payload an object was read from, which a Workflow whose persisted shapes predate the typed model projects from.
def _payload() -> Any:
    return field(default_factory=dict, compare=False, repr=False, kw_only=True)


class PullRequestState(StrEnum):
    """Whether the pull request is open or closed; a merged pull request is closed."""

    OPEN = "open"
    CLOSED = "closed"


class Mergeability(StrEnum):
    """UNKNOWN: GitHub has not computed it yet, so the pull request is neither mergeable nor conflicting."""

    MERGEABLE = "mergeable"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"

    @classmethod
    def of(cls, computed: bool | None) -> Mergeability:
        """The mergeability GitHub reports: true, false, or not yet computed (null or absent)."""
        if computed is None:
            return cls.UNKNOWN
        return cls.MERGEABLE if computed else cls.CONFLICTING


class MergeState(StrEnum):
    """GitHub's merge state of the pull request; anything it does not name is UNKNOWN."""

    CLEAN = "clean"
    DIRTY = "dirty"
    BLOCKED = "blocked"
    BEHIND = "behind"
    UNSTABLE = "unstable"
    HAS_HOOKS = "has_hooks"
    DRAFT = "draft"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> MergeState:
        return cls.UNKNOWN


class ReviewVerdict(StrEnum):
    """What a review says, or PENDING while its author has not submitted it."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    COMMENTED = "COMMENTED"
    DISMISSED = "DISMISSED"
    PENDING = "PENDING"


@dataclass(frozen=True)
class PullRequest:
    """The pull request itself, without its discussion or checks."""

    ref: IssueRef
    identity: IssueIdentity
    title: str
    body: str
    url: str
    state: PullRequestState
    draft: bool
    merged: bool
    base_ref: str
    head_ref: str
    head_sha: str
    head_repository: str | None
    author: str
    mergeability: Mergeability
    merge_state: MergeState
    requested_reviewers: tuple[str, ...] = ()
    payload: Mapping[str, Any] = _payload()

    @property
    def number(self) -> int:
        """The pull request number within its repository."""
        return self.ref.number

    @property
    def is_open(self) -> bool:
        """The pull request is open."""
        return self.state is PullRequestState.OPEN

    @property
    def mergeable(self) -> bool:
        """GitHub computed that it merges cleanly; false while mergeability is unknown."""
        return self.mergeability is Mergeability.MERGEABLE

    @property
    def conflicting(self) -> bool:
        """GitHub computed that it conflicts with its base; false while mergeability is unknown."""
        return self.mergeability is Mergeability.CONFLICTING


@dataclass(frozen=True)
class ReviewComment:
    """One comment of a review thread, anchored to a diff location; never a conversation Comment."""

    id: str
    database_id: int | None
    author: str | None
    author_association: str
    body: str
    created_at: str
    diff_hunk: str
    url: str
    review_state: ReviewVerdict | None


@dataclass(frozen=True)
class ReviewThread:
    """A review thread with its resolved and outdated state and every one of its submitted comments."""

    id: str
    resolved: bool
    outdated: bool
    path: str
    line: int | None
    original_line: int | None
    comments: tuple[ReviewComment, ...]


@dataclass(frozen=True)
class PullRequestReview:
    """One review: who gave it, its verdict, and the commit it was submitted against."""

    id: int
    reviewer: str
    verdict: ReviewVerdict
    commit: str
    body: str
    submitted_at: str | None
    payload: Mapping[str, Any] = _payload()


class CheckKind(StrEnum):
    """Where a check result comes from."""

    CHECK_RUN = "check_run"
    STATUS = "status"
    WORKFLOW_RUN = "workflow_run"


class CheckOutcome(StrEnum):
    """PENDING: still running or waiting. PASSED: succeeded, neutral, or skipped. FAILED: anything else that finished."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class CheckResult:
    """One check, commit status, or workflow run, tied to the commit it ran against."""

    kind: CheckKind
    id: int
    name: str
    commit: str
    outcome: CheckOutcome
    payload: Mapping[str, Any] = _payload()

    def stale(self, head_sha: str) -> bool:
        """It ran against a commit other than `head_sha`."""
        return self.commit != head_sha

    def passing(self, head_sha: str) -> bool:
        """It passed on `head_sha`; a stale result never passes."""
        return self.outcome is CheckOutcome.PASSED and not self.stale(head_sha)


@dataclass(frozen=True)
class PullRequestSnapshot:
    """An immutable read of a pull request; a closed one carries no discussion or checks."""

    pull_request: PullRequest
    threads: tuple[ReviewThread, ...] = ()
    reviews: tuple[PullRequestReview, ...] = ()
    comments: tuple[Comment, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    review_decision: str = ""
    merge_state_status: str = ""

    def checks_of(self, kind: CheckKind) -> tuple[CheckResult, ...]:
        """The check results of `kind`, in the order GitHub listed them."""
        return tuple(check for check in self.checks if check.kind is kind)

    @property
    def current_checks(self) -> tuple[CheckResult, ...]:
        """The check results produced for the current head commit."""
        return tuple(check for check in self.checks if not check.stale(self.pull_request.head_sha))

    @property
    def stale_checks(self) -> tuple[CheckResult, ...]:
        """The check results produced for any other commit."""
        return tuple(check for check in self.checks if check.stale(self.pull_request.head_sha))


@dataclass(frozen=True)
class PublishedPullRequest:
    """A pull request as `gh` reports it when it is created or found by its head branch."""

    number: int
    title: str
    url: str
    branch: str
    body: str = ""
