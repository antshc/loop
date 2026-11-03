"""Parses the last JSON object of the agent output into the reply dataclass."""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from dataclasses import MISSING, fields, is_dataclass
from typing import Any, get_args, get_origin, get_type_hints

from ..context import AgentCallContext
from ..errors import ReplyMismatch
from ..middleware import Next


class JsonReplyMiddleware:
    """Post-agent: sets `ctx.reply` or raises `ReplyMismatch`."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Decodes `ctx.payload`, else the last JSON object of the output."""
        await next(ctx)
        if ctx.result is None:
            raise ReplyMismatch("no agent result to parse")
        payload = (
            ctx.payload
            if ctx.payload is not None
            else last_json_object(ctx.result.output)
        )
        ctx.reply = to_typed(ctx.request.reply_type, payload)


def last_json_object(
    text: str, accept: Callable[[dict[str, Any]], bool] = lambda _: True
) -> dict[str, Any]:
    """The last top-level JSON object in `text` that `accept` holds for."""
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict) and accept(value):
            found = value
        index = text.find("{", end)
    if found is None:
        raise ReplyMismatch("output has no JSON object")
    return found


def to_typed(annotation: Any, value: Any, path: str = "$") -> Any:
    """`value` checked against `annotation`, with dataclasses built from objects."""
    if is_dataclass(annotation) and isinstance(annotation, type):
        return _dataclass(annotation, value, path)
    origin = get_origin(annotation)
    if origin in (list, tuple):
        if not isinstance(value, list):
            raise ReplyMismatch(f"{path} must be a list")
        (item,) = get_args(annotation)[:1]
        items = [to_typed(item, entry, f"{path}[{i}]") for i, entry in enumerate(value)]
        return items if origin is list else tuple(items)
    if origin is types.UnionType:
        return _union(annotation, value, path)
    if annotation is type(None):
        if value is not None:
            raise ReplyMismatch(f"{path} must be null")
        return None
    # bool is an int in Python but not a valid JSON number here.
    if not isinstance(value, annotation) or (
        isinstance(value, bool) and annotation is not bool
    ):
        raise ReplyMismatch(f"{path} must be {annotation.__name__}")
    return value


def _dataclass(cls: type, value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        raise ReplyMismatch(f"{path} must be an object")
    hints = get_type_hints(cls)
    missing = [
        field.name
        for field in fields(cls)
        if field.name not in value
        and field.default is MISSING
        and field.default_factory is MISSING
    ]
    if missing:
        raise ReplyMismatch(f"{path} is missing {', '.join(missing)}")
    return cls(
        **{
            field.name: to_typed(
                hints[field.name], value[field.name], f"{path}.{field.name}"
            )
            for field in fields(cls)
            if field.name in value
        }
    )


def _union(annotation: Any, value: Any, path: str) -> Any:
    for option in get_args(annotation):
        try:
            return to_typed(option, value, path)
        except ReplyMismatch:
            continue
    raise ReplyMismatch(f"{path} matches no option of {annotation}")
