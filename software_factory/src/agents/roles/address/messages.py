"""The address agent's requests and replies, one pair per mode."""

from __future__ import annotations

from dataclasses import dataclass

from ...contract import Json, Message


@dataclass(frozen=True)
class FeedbackIssue(Message):
    """One underlying concern and the feedback keys it covers; `statement` is absent in a request that omits it."""

    id: str
    statement: str | None
    keys: tuple[str, ...]


@dataclass(frozen=True)
class GroupRequest(Message):
    """Feedback items to group into issues; the agent only reads."""

    items: Json


@dataclass(frozen=True)
class Grouped(Message):
    """The issues the agent grouped the feedback into."""

    issues: tuple[FeedbackIssue, ...]


@dataclass(frozen=True)
class ResolveRequest(Message):
    """One issue and its feedback items to resolve in the worktree."""

    issue: FeedbackIssue
    items: Json


@dataclass(frozen=True)
class FeedbackReply(Message):
    """The plain reply drafted for one feedback key."""

    key: str
    body: str


@dataclass(frozen=True)
class Resolved(Message):
    """The verdict on one issue (`fix-needed`, `no-change-needed` or `unclear`), its `path:line` evidence and drafted replies."""

    verdict: str
    evidence: tuple[str, ...]
    replies: tuple[FeedbackReply, ...]
    reason: str = ""
