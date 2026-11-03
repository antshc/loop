"""Describes the reply dataclass as JSON and fills `{{RESPONSE_FORMAT}}`."""

from __future__ import annotations

import json
import types
from dataclasses import fields, is_dataclass
from typing import Any, get_args, get_origin, get_type_hints

from ..context import AgentCallContext
from ..middleware import Next


class ResponseFormatMiddleware:
    """Pre-agent: the reply type is the single source of the response format."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Sets RESPONSE_FORMAT from `ctx.request.reply_type`."""
        shape = json.dumps(describe(ctx.request.reply_type), indent=2)
        ctx.args["RESPONSE_FORMAT"] = (
            "End your output with exactly one JSON object of this shape:\n"
            f"```json\n{shape}\n```"
        )
        await next(ctx)


def describe(annotation: Any) -> Any:
    """A JSON example of `annotation` whose leaves name their types."""
    if is_dataclass(annotation) and isinstance(annotation, type):
        hints = get_type_hints(annotation)
        return {field.name: describe(hints[field.name]) for field in fields(annotation)}
    origin = get_origin(annotation)
    if origin in (list, tuple):
        return [describe(get_args(annotation)[0])]
    if origin is types.UnionType:
        return " | ".join(str(describe(arg)) for arg in get_args(annotation))
    if annotation is type(None):
        return "null"
    return getattr(annotation, "__name__", str(annotation))
