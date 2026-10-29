"""Resolves a Stage, Selector or workspace-opener input from the scope: upstream Events, context, loop item, workspace or service."""

from __future__ import annotations

import functools
from typing import Any

from ..facts import FieldKind, InputField, input_fields, place_plain
from .scope import Scope


@functools.cache
def _fields(input_type: type) -> tuple[InputField, ...]:
    return input_fields(input_type.__name__, input_type)


def resolve[I](input_type: type[I], scope: Scope, reads: tuple[tuple[type, str], ...] = ()) -> I:
    """Builds `input_type` from what `scope` holds; `reads` binds an Event to the Stage that produced it."""
    bindings = dict(reads)
    values = {field.name: _value(field, scope, bindings) for field in _fields(input_type)}
    return input_type(**values)


def _value(field: InputField, scope: Scope, bindings: dict[type, str]) -> Any:
    run = scope.run
    kind = place_plain(field, run.workflow.context_type, scope.item_types) if field.kind is FieldKind.PLAIN else field.kind
    match kind:
        case FieldKind.EVENT:
            return _required_event(field, scope, bindings)
        case FieldKind.OPTIONAL:
            return scope.latest(field.shape, _producer(field, bindings))
        case FieldKind.ALL:
            return scope.every(field.shape)
        case FieldKind.HISTORY:
            (event,) = field.shape
            return run.log.history(event)
        case FieldKind.WORKSPACE:
            return scope.workspace
        case FieldKind.CONTEXT:
            return run.context
        case FieldKind.ITEM:
            return scope.item_value(field.type_)
    return run.services.get(field.type_)


def _producer(field: InputField, bindings: dict[type, str]) -> str | None:
    return next((producer for event, producer in bindings.items() if event in field.shape), None)


def _required_event(field: InputField, scope: Scope, bindings: dict[type, str]) -> Any:
    event = scope.latest(field.shape, _producer(field, bindings))
    if event is None:
        names = " | ".join(sorted(event_type.__name__ for event_type in field.shape))
        raise LookupError(f"field '{field.name}' needs {names}, but none was produced in this run")
    return event
