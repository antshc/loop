"""The Events of selecting, grouping, resolving and publishing a pull request's review feedback."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from loop import Event, Json


@dataclass(frozen=True)
class TrustPolicy:
    """Whose feedback is accepted: author associations and review-automation logins; the acting account's own login is always accepted."""

    associations: frozenset[str]
    bots: frozenset[str]


@dataclass(frozen=True)
class FeedbackBatch(Event):
    """The pending trusted feedback of one pull request head, as the item dictionaries the address agent is shown verbatim; `ci_failing` is the CI state of that head."""

    head: str
    ci_failing: bool
    items: tuple[dict[str, Json], ...]

    @property
    def keys(self) -> tuple[str, ...]:
        """The feedback keys of the items, in order."""
        return tuple(str(item["key"]) for item in self.items)

    @property
    def token(self) -> str:
        """Identifies the batch across runs by the digest of its feedback keys, which our own push to the head does not change."""
        return hashlib.sha256("\n".join(sorted(self.keys)).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class GroupedIssue:
    """One underlying concern and the feedback keys it covers; `statement` is absent when the agent gave none."""

    id: str
    statement: str | None
    keys: tuple[str, ...]


@dataclass(frozen=True)
class FeedbackGrouped(Event):
    """The feedback of batch `batch` grouped into issues; `base` is the pull request head the batch opened at."""

    batch: str
    base: str
    issues: tuple[GroupedIssue, ...]


@dataclass(frozen=True)
class ReplyDraft:
    """The plain reply drafted for one feedback key."""

    key: str
    body: str


@dataclass(frozen=True)
class IssueResolved(Event):
    """Issue `issue` of batch `batch` was resolved with `verdict` (`fix-needed` or `no-change-needed`) and its drafted replies."""

    batch: str
    issue: str
    verdict: str
    replies: tuple[ReplyDraft, ...]


@dataclass(frozen=True)
class IssueUnclear(Event):
    """The reviewer must clarify issue `issue` of batch `batch`; the replies drafted for its feedback still get posted."""

    batch: str
    issue: str
    reason: str
    replies: tuple[ReplyDraft, ...]


@dataclass(frozen=True)
class FeedbackHandled(Event):
    """The feedback `keys` got their replies against the pull request head `head`; later polls do not select them again."""

    keys: tuple[str, ...]
    head: str
