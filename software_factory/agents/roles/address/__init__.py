"""The address role: groups review feedback into issues and resolves one issue at a time."""

from __future__ import annotations

from typing import Any

from ... import _kit
from ...contract import ReplyMismatch, TypedAgent
from .messages import FeedbackIssue, FeedbackReply, Grouped, GroupRequest, Resolved, ResolveRequest

_VERDICTS = ("fix-needed", "no-change-needed", "unclear")

_PROFILE = _kit.profile_with_instructions(__name__, "address", timeout_sec=1800)


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReplyMismatch(f"{what} must be a list of strings: {value!r}")
    return tuple(value)


def _issue(item: Any) -> FeedbackIssue:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("statement"), str):
        raise ReplyMismatch(f"invalid issue: {item!r}")
    return FeedbackIssue(item["id"], item["statement"], _strings(item.get("keys"), "issue keys"))


def _decode_group(request: GroupRequest, output: str) -> Grouped:
    issues = _kit.read_object(output).get("issues")
    if not isinstance(issues, list):
        raise ReplyMismatch(f"issues must be a list: {issues!r}")
    return Grouped(tuple(_issue(item) for item in issues))


def _reply(key: Any, body: Any) -> FeedbackReply:
    if not isinstance(key, str) or not isinstance(body, str):
        raise ReplyMismatch(f"reply must map a feedback key to text: {key!r}")
    return FeedbackReply(key, body)


def _decode_resolve(request: ResolveRequest, output: str) -> Resolved:
    result = _kit.read_object(output)
    verdict, replies, reason = result.get("verdict"), result.get("replies"), result.get("reason", "")
    if verdict not in _VERDICTS:
        raise ReplyMismatch(f"invalid verdict: {verdict!r}")
    if not isinstance(replies, dict) or not isinstance(reason, str):
        raise ReplyMismatch(f"resolution needs a replies object and a text reason: {result!r}")
    evidence = _strings(result.get("evidence", []), "evidence")
    return Resolved(verdict, evidence, tuple(_reply(key, body) for key, body in replies.items()), reason)


GROUP_FEEDBACK: TypedAgent[GroupRequest, Grouped] = _kit.mode_agent("address", "group", profile=_PROFILE, decode=_decode_group)
RESOLVE_ISSUE: TypedAgent[ResolveRequest, Resolved] = _kit.mode_agent("address", "resolve", profile=_PROFILE, decode=_decode_resolve)

__all__ = [
    "GROUP_FEEDBACK", "RESOLVE_ISSUE", "FeedbackIssue", "FeedbackReply", "GroupRequest", "Grouped", "Resolved", "ResolveRequest",
]
