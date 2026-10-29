"""The ledger's value codec: dataclasses, enums, paths and plain JSON values to and from JSON-compatible values."""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from software_factory.agents import Json


def type_name(type_: type) -> str:
    """The stable tag a dataclass is stored under."""
    return f"{type_.__module__}.{type_.__qualname__}"


def encode(value: Any) -> Json:
    """The JSON-compatible form of `value`; a dataclass becomes {"type", "fields"}, an unknown object its repr."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return encode(value.value)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {field.name: encode(getattr(value, field.name)) for field in dataclasses.fields(value)}
        return {"type": type_name(type(value)), "fields": fields}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [encode(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): encode(item) for key, item in value.items()}
    return {"repr": repr(value)}


def is_tagged(value: Json, type_: type) -> bool:
    """Whether `value` is a stored dataclass of exactly `type_`."""
    return isinstance(value, dict) and value.get("type") == type_name(type_) and isinstance(value.get("fields"), dict)


def decode(value: Json, type_: Any) -> Any:
    """Rebuilds a value of the annotated `type_` from its stored form."""
    origin = typing.get_origin(type_)
    if isinstance(type_, typing.TypeAliasType):
        return decode(value, type_.__value__)
    if origin in (typing.Union, types.UnionType):
        return _decode_union(value, typing.get_args(type_))
    if origin in (tuple, list, frozenset, set):
        return _decode_sequence(value, origin, typing.get_args(type_))
    if origin is dict:
        return _decode_mapping(value, typing.get_args(type_))
    if isinstance(type_, type):
        return _decode_class(value, type_)
    return value


def _decode_union(value: Json, arguments: tuple[Any, ...]) -> Any:
    if value is None and type(None) in arguments:
        return None
    for argument in arguments:
        if argument is type(None):
            continue
        if isinstance(argument, type) and dataclasses.is_dataclass(argument) and not is_tagged(value, argument):
            continue
        return decode(value, argument)
    return value


def _decode_sequence(value: Json, origin: type, arguments: tuple[Any, ...]) -> Any:
    items = value if isinstance(value, list) else []
    element = arguments[0] if arguments and arguments[0] is not Ellipsis else Any
    if origin is tuple and len(arguments) > 1 and Ellipsis not in arguments:
        return tuple(decode(item, argument) for item, argument in zip(items, arguments))
    return origin(decode(item, element) for item in items)


def _decode_mapping(value: Json, arguments: tuple[Any, ...]) -> dict[str, Any]:
    items = value if isinstance(value, dict) else {}
    element = arguments[1] if len(arguments) > 1 else Any
    return {key: decode(item, element) for key, item in items.items()}


def _decode_class(value: Json, type_: type) -> Any:
    if dataclasses.is_dataclass(type_):
        return _decode_dataclass(value, type_)
    if issubclass(type_, Enum):
        return type_(value)
    if issubclass(type_, Path):
        return type_(str(value))
    return value


def _decode_dataclass(value: Json, type_: type) -> Any:
    if not is_tagged(value, type_):
        raise ValueError(f"stored value is not a {type_.__name__}")
    stored = value["fields"]  # type: ignore[index]
    hints = typing.get_type_hints(type_)
    return type_(**{field.name: decode(stored[field.name], hints[field.name]) for field in dataclasses.fields(type_) if field.name in stored})
