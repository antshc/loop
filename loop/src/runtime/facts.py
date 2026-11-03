"""How a Stage input resolves: field kinds, the closed sums of Events, and the facts about what upstream Stages produced."""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..builder import AgentWorkspace
from .event import All, DefinitionError, Event, History


class FieldKind(StrEnum):
    """How the runtime resolves one input field."""

    EVENT = "event"
    OPTIONAL = "optional event"
    ALL = "all events"
    HISTORY = "event history"
    WORKSPACE = "workspace"
    CONTEXT = "context"
    ITEM = "item"
    SERVICE = "service"
    PLAIN = "plain"


@dataclass(frozen=True)
class InputField:
    """One field of a Stage input; `PLAIN` fields become context, item or service where the Stage is placed."""

    name: str
    kind: FieldKind
    shape: frozenset[type[Event]]
    type_: Any


@dataclass(frozen=True)
class Fact:
    """An upstream Stage produces one of the Events in `shape`; `definite` when every path reaching here produced it."""

    shape: frozenset[type[Event]]
    producer: str
    definite: bool


def is_event_class(value: object) -> bool:
    """Whether `value` is an Event subclass."""
    return isinstance(value, type) and issubclass(value, Event)


def event_shape(annotation: Any) -> frozenset[type[Event]] | None:
    """The Event classes an annotation names (one Event, or a closed sum as a union or type alias); None for anything else."""
    if isinstance(annotation, typing.TypeAliasType):
        return event_shape(annotation.__value__)
    if is_event_class(annotation):
        return frozenset({annotation})
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        shapes = [event_shape(argument) for argument in typing.get_args(annotation)]
        if shapes and all(shape is not None for shape in shapes):
            return frozenset().union(*shapes)
    return None


def closed_sum(annotation: Any) -> frozenset[type[Event]] | None:
    """The variants of a closed sum of at least two Events; None when the annotation is not one."""
    shape = event_shape(annotation)
    return shape if shape is not None and len(shape) >= 2 else None


def input_fields(owner: str, input_type: type) -> tuple[InputField, ...]:
    """The classified fields of a frozen dataclass input; raises DefinitionError naming `owner` for an unusable input or field."""
    if not (isinstance(input_type, type) and dataclasses.is_dataclass(input_type)):
        raise DefinitionError(
            f"{owner}: input {input_type!r} must be a frozen dataclass"
        )
    if not input_type.__dataclass_params__.frozen:  # type: ignore[attr-defined]
        raise DefinitionError(
            f"{owner}: input {input_type.__name__} must be a frozen dataclass"
        )
    try:
        hints = typing.get_type_hints(input_type)
    except NameError as exception:
        raise DefinitionError(
            f"{owner}: input {input_type.__name__} has an unresolvable annotation ({exception})"
        ) from exception
    return tuple(
        _classify(owner, field.name, hints[field.name])
        for field in dataclasses.fields(input_type)
    )


def _classify(owner: str, name: str, annotation: Any) -> InputField:
    """The field kind for one annotation."""
    origin = typing.get_origin(annotation)
    if annotation in (All, History):
        raise DefinitionError(
            f"{owner} field '{name}': {annotation.__name__} needs an Event type argument"
        )
    if origin in (All, History):
        return _marker_field(owner, name, annotation)
    if origin in (typing.Union, types.UnionType) and type(None) in typing.get_args(
        annotation
    ):
        return _optional_field(owner, name, annotation)
    shape = event_shape(annotation)
    if shape is not None:
        return InputField(name, FieldKind.EVENT, shape, annotation)
    if annotation is AgentWorkspace:
        return InputField(name, FieldKind.WORKSPACE, frozenset(), annotation)
    if not isinstance(annotation, type) or annotation.__module__ == "builtins":
        raise DefinitionError(
            f"{owner} field '{name}': {annotation!r} cannot be resolved; primitives and untyped values are rejected, "
            "use an Event, the context, the loop item, the agent workspace or a service type"
        )
    return InputField(name, FieldKind.PLAIN, frozenset(), annotation)


def _marker_field(owner: str, name: str, annotation: Any) -> InputField:
    """An `All[T]` or `History[T]` field."""
    (argument,) = typing.get_args(annotation)
    if not is_event_class(argument):
        raise DefinitionError(
            f"{owner} field '{name}': {typing.get_origin(annotation).__name__} reads Events, not {argument!r}"
        )
    kind = FieldKind.ALL if typing.get_origin(annotation) is All else FieldKind.HISTORY
    return InputField(name, kind, frozenset({argument}), annotation)


def _optional_field(owner: str, name: str, annotation: Any) -> InputField:
    """A `T | None` field."""
    rest = [
        argument
        for argument in typing.get_args(annotation)
        if argument is not type(None)
    ]
    shapes = [event_shape(argument) for argument in rest]
    if not shapes or any(shape is None for shape in shapes):
        raise DefinitionError(
            f"{owner} field '{name}': only an Event can be optional, not {annotation!r}"
        )
    return InputField(name, FieldKind.OPTIONAL, frozenset().union(*shapes), annotation)


def place_plain(
    field: InputField, context: type | None, items: Sequence[type]
) -> FieldKind:
    """Where a plain field resolves: the nearest enclosing loop item, else the Workflow context, else a service."""
    for item in reversed(items):
        if _is_same_or_base(field.type_, item):
            return FieldKind.ITEM
    if context is not None and _is_same_or_base(field.type_, context):
        return FieldKind.CONTEXT
    return FieldKind.SERVICE


def _is_same_or_base(declared: type, actual: type) -> bool:
    """Whether a value of type `actual` satisfies the declared type."""
    return isinstance(actual, type) and issubclass(actual, declared)


def producers_of(facts: Sequence[Fact], event: type[Event]) -> tuple[str, ...]:
    """The distinct producers, in order, whose output may be `event`."""
    return tuple(dict.fromkeys(fact.producer for fact in facts if event in fact.shape))


def merge(paths: Sequence[Sequence[Fact]]) -> tuple[Fact, ...]:
    """The facts after control paths meet: a fact is definite only when every path holds it definitely."""
    merged: dict[tuple[frozenset[type[Event]], str], bool] = {}
    for path in paths:
        for fact in path:
            merged.setdefault((fact.shape, fact.producer), True)
    for (shape, producer), _ in list(merged.items()):
        merged[shape, producer] = all(
            any(
                fact.shape == shape and fact.producer == producer and fact.definite
                for fact in path
            )
            for path in paths
        )
    return tuple(
        Fact(shape, producer, definite)
        for (shape, producer), definite in merged.items()
    )
