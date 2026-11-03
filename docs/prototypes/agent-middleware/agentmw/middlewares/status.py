"""The software factory's `completed | failed` status envelope around the reply."""

from __future__ import annotations

import json
from typing import Any

from ..context import AgentCallContext
from ..errors import AgentDeclined, ReplyMismatch
from ..middleware import Next
from .json_reply import last_json_object
from .response_format import describe

_STATUSES = ("completed", "failed")


class StatusFormatMiddleware:
    """Pre-agent: RESPONSE_FORMAT as the envelope, with the reply type as its result."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Sets RESPONSE_FORMAT; echoes `metadata["identifier"]` when the request has one."""
        completed = _envelope(ctx, "completed", describe(ctx.request.reply_type))
        failed = _envelope(ctx, "failed", {"reason": "<why it failed>"})
        ctx.args["RESPONSE_FORMAT"] = (
            "End your final message with exactly one JSON object:\n\n"
            f"```json\n{completed}\n```\n\n"
            f"or, if you cannot finish:\n\n```json\n{failed}\n```"
        )
        await next(ctx)


class StatusEnvelopeMiddleware:
    """Post-agent: unwraps the envelope into `ctx.payload`; `failed` raises `AgentDeclined`."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """A malformed envelope or a wrong identifier is a `ReplyMismatch`."""
        await next(ctx)
        if ctx.result is None:
            raise ReplyMismatch("no agent result to parse")
        envelope = last_json_object(ctx.result.output, lambda value: "status" in value)
        _check_identifier(ctx, envelope)
        result = envelope.get("result")
        if envelope["status"] not in _STATUSES or not isinstance(result, dict):
            raise ReplyMismatch(f"malformed response envelope: {envelope!r}")
        if envelope["status"] == "failed":
            raise AgentDeclined(ctx.role, _reason(result))
        ctx.payload = result


def _envelope(ctx: AgentCallContext, status: str, result: Any) -> str:
    identifier = ctx.request.metadata.get("identifier")
    head = {} if identifier is None else {"identifier": identifier}
    return json.dumps({**head, "status": status, "result": result}, indent=2)


def _check_identifier(ctx: AgentCallContext, envelope: dict[str, Any]) -> None:
    # An envelope that omits the identifier is accepted; only a different one is wrong.
    expected = ctx.request.metadata.get("identifier")
    if expected is not None and envelope.get("identifier", expected) != expected:
        raise ReplyMismatch(
            f"response identifier {envelope['identifier']!r} does not match {expected!r}"
        )


def _reason(result: dict[str, Any]) -> str:
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ReplyMismatch(f"failed response is missing result.reason: {result!r}")
    return reason
