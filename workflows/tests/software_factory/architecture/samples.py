"""Sample values built from type hints alone, so structural tests can construct any dataclass, Stage factory or definition factory without a hand-kept list; imports nothing of the factory."""

from __future__ import annotations

import dataclasses
import enum
import inspect
import types
import typing
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

_MAX_DEPTH = 8

_SCALARS = {bool: True, int: 1, float: 1.0, str: "x", Path: Path(".")}


def sample_of(hint: Any, depth: int = 0) -> Any:
    """A value that satisfies `hint`; raises TypeError for a hint it cannot satisfy."""
    if depth > _MAX_DEPTH:
        raise TypeError(f"no sample of {hint!r} within {_MAX_DEPTH} levels")
    if isinstance(hint, typing.TypeAliasType):
        return sample_of(hint.__value__, depth + 1)
    origin = typing.get_origin(hint)
    if origin is not None:
        return _generic_sample(origin, typing.get_args(hint), depth)
    return _plain_sample(hint, depth)


def _plain_sample(hint: Any, depth: int) -> Any:
    if hint is Any:
        return "x"
    if hint is None or hint is type(None):
        return None
    if hint in _SCALARS:
        return _SCALARS[hint]
    if inspect.isclass(hint) and issubclass(hint, enum.Enum):
        return next(iter(hint))
    if dataclasses.is_dataclass(hint):
        return sample_dataclass(hint, depth)
    raise TypeError(f"no sample of {hint!r}")


def _generic_sample(origin: Any, arguments: tuple[Any, ...], depth: int) -> Any:
    if origin in (types.UnionType, typing.Union):
        return sample_of(
            next(argument for argument in arguments if argument is not type(None)),
            depth + 1,
        )
    if origin is typing.Literal:
        return arguments[0]
    if origin is tuple:
        return _tuple_sample(arguments, depth)
    if origin in (list, set, frozenset, Sequence, Iterable, Collection):
        return (origin if origin in (list, set, frozenset) else tuple)(
            [sample_of(arguments[0], depth + 1)]
        )
    if origin in (dict, Mapping):
        return {sample_of(arguments[0], depth + 1): sample_of(arguments[1], depth + 1)}
    if origin is Callable:
        return lambda *_, **__: None
    raise TypeError(f"no sample of {origin!r}")


def _tuple_sample(arguments: tuple[Any, ...], depth: int) -> tuple[Any, ...]:
    if len(arguments) == 2 and arguments[1] is Ellipsis:
        return (sample_of(arguments[0], depth + 1),)
    return tuple(sample_of(argument, depth + 1) for argument in arguments)


def sample_dataclass(cls: type, depth: int = 0) -> Any:
    """An instance of the dataclass `cls` whose required fields hold samples of their hints."""
    hints = typing.get_type_hints(cls)
    required = [
        field.name
        for field in dataclasses.fields(cls)
        if field.init and _is_required(field)
    ]
    return cls(**{name: sample_of(hints[name], depth + 1) for name in required})


def _is_required(field: dataclasses.Field) -> bool:
    return (
        field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    )


def call_with_samples(function: Callable[..., Any]) -> Any:
    """Calls `function` with a sample for every parameter without a default."""
    hints = typing.get_type_hints(function)
    parameters = inspect.signature(function).parameters.values()
    return function(
        **{
            p.name: sample_of(hints[p.name])
            for p in parameters
            if p.default is inspect.Parameter.empty
        }
    )
