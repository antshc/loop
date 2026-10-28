"""A GitHub issue as an immutable snapshot, with its Comments and what could not be read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from ..identity import CommentRef, IssueIdentity, IssueRef


class IssueState(StrEnum):
    """Whether the issue is open or closed."""

    OPEN = "open"
    CLOSED = "closed"


class CloseReason(StrEnum):
    """Why GitHub says the issue was closed."""

    COMPLETED = "completed"
    NOT_PLANNED = "not_planned"
    DUPLICATE = "duplicate"


class GapState(StrEnum):
    """INCOMPLETE: a page failed or was cut short. UNKNOWN: GitHub denied access, so the state is not empty but unseen."""

    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReadGap:
    """One part of an issue read (labels, comments, sub_issues) that is not fully known."""

    part: str
    state: GapState
    reason: str = ""


@dataclass(frozen=True)
class Comment:
    """One conversation message, addressed by its issue and its own id; the single Comment type for issues and pull requests."""

    ref: CommentRef
    author: str
    body: str
    created_at: str
    # The GitHub payload it was read from; a Workflow whose persisted shapes predate the typed model projects from it.
    payload: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class Issue:
    """An immutable snapshot of one issue; a part listed in `gaps` was not fully read and must not be treated as empty."""

    ref: IssueRef
    identity: IssueIdentity
    title: str
    body: str
    url: str
    state: IssueState
    close_reason: CloseReason | None = None
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    issue_type: str | None = None
    comments: tuple[Comment, ...] = ()
    gaps: tuple[ReadGap, ...] = ()

    @property
    def number(self) -> int:
        """The issue number within its repository."""
        return self.ref.number

    @property
    def complete(self) -> bool:
        """Every part was fully read."""
        return not self.gaps

    def with_label(self, label: str) -> Issue:
        """This snapshot carrying `label`."""
        return self if label in self.labels else replace(self, labels=(*self.labels, label))

    def closed(self, reason: CloseReason | None = None) -> Issue:
        """This snapshot closed."""
        return replace(self, state=IssueState.CLOSED, close_reason=reason)
