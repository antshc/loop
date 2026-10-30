"""The span seam the runtime reports through: a console log, an in-memory recording for tests, and a no-op."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Protocol

type Attributes = Mapping[str, str | int | bool]

LOGGER = logging.getLogger("loop.runtime")

_OUTCOME_KEYS = ("factory.stage.outcome", "factory.scope.status", "factory.run.status")
_REASON_KEYS = ("factory.stage.reason", "factory.run.reason")


class SpanHandle(Protocol):
    """One open span: attributes, events and the failure mark."""

    def set(self, **attributes: str | int | bool) -> None: ...

    def event(self, name: str, **attributes: str | int | bool) -> None: ...

    def fail(self, reason: str) -> None: ...


class Spans(Protocol):
    """Opens the runtime's spans; the span ends, and its parent is the span open when it started, when the context exits."""

    def open(self, name: str, attributes: Attributes, *, role: str) -> AbstractContextManager[SpanHandle]: ...


class _NoSpan:
    def set(self, **attributes: str | int | bool) -> None:
        return None

    def event(self, name: str, **attributes: str | int | bool) -> None:
        return None

    def fail(self, reason: str) -> None:
        return None


class NullSpans:
    """Reports nothing."""

    @contextmanager
    def open(self, name: str, attributes: Attributes, *, role: str) -> Iterator[SpanHandle]:
        """Opens a span that discards everything."""
        yield _NoSpan()


@dataclass(frozen=True)
class RecordedEvent:
    """A span event as recorded."""

    name: str
    attributes: dict[str, str | int | bool]


@dataclass
class RecordedSpan:
    """A finished or open span with its attributes, events and children."""

    name: str
    role: str
    attributes: dict[str, str | int | bool]
    events: list[RecordedEvent] = field(default_factory=list)
    children: list[RecordedSpan] = field(default_factory=list)
    error: str | None = None

    def set(self, **attributes: str | int | bool) -> None:
        """Adds or replaces attributes."""
        self.attributes.update(attributes)

    def event(self, name: str, **attributes: str | int | bool) -> None:
        """Records a span event."""
        self.events.append(RecordedEvent(name, dict(attributes)))

    def fail(self, reason: str) -> None:
        """Marks the span as an error."""
        self.error = reason


class RecordingSpans:
    """Keeps every span in memory as a tree, for tests."""

    def __init__(self) -> None:
        self.roots: list[RecordedSpan] = []
        self._open: list[RecordedSpan] = []

    @contextmanager
    def open(self, name: str, attributes: Attributes, *, role: str) -> Iterator[SpanHandle]:
        """Opens a span under the span open now; an exception leaving the block marks it failed."""
        span = RecordedSpan(name, role, dict(attributes))
        (self._open[-1].children if self._open else self.roots).append(span)
        self._open.append(span)
        try:
            yield span
        except BaseException as exception:
            span.fail(f"{type(exception).__name__}: {exception}")
            raise
        finally:
            self._open.pop()

    def tree(self) -> tuple[RecordedSpan, ...]:
        """The root spans, each with its children in the order they started."""
        return tuple(self.roots)

    def find(self, name: str) -> list[RecordedSpan]:
        """Every span called `name`, in the order they started."""
        return [span for span in self._walk(self.roots) if span.name == name]

    def attempts(self, stage_id: str) -> list[RecordedSpan]:
        """The attempt spans of one Stage, in the order they started."""
        return [span for span in self.find(stage_id) if span.attributes.get("factory.stage.id") == stage_id]

    def events(self, stage_id: str, name: str) -> list[dict[str, str | int | bool]]:
        """The attributes of every `name` event on the Stage's attempt spans, in order."""
        return [event.attributes for span in self.attempts(stage_id) for event in span.events if event.name == name]

    def _walk(self, spans: list[RecordedSpan]) -> Iterator[RecordedSpan]:
        for span in spans:
            yield span
            yield from self._walk(span.children)


class LogSpans:
    """Console: one INFO line per finished span, such as 'ticket #12 · implement-ticket attempt 2 · fail · reason'."""

    def __init__(self, logger: logging.Logger = LOGGER) -> None:
        self._logger = logger
        self._names: list[str] = []

    @contextmanager
    def open(self, name: str, attributes: Attributes, *, role: str) -> Iterator[SpanHandle]:
        """Opens a span that logs one line when it ends."""
        span = RecordedSpan(name, role, dict(attributes))
        self._names.append(_label(span))
        try:
            yield span
        except BaseException as exception:
            span.fail(f"{type(exception).__name__}: {exception}")
            raise
        finally:
            self._logger.info(self._line(span))
            self._names.pop()

    def _line(self, span: RecordedSpan) -> str:
        """The scope path below the run, then the span's own label, outcome and reason."""
        names = [*self._names[1:-1], _label(span)] if len(self._names) > 1 else [_label(span)]
        outcome = next((str(span.attributes[key]) for key in _OUTCOME_KEYS if key in span.attributes), None)
        reason = span.error or next((str(span.attributes[key]) for key in _REASON_KEYS if key in span.attributes), None)
        return " · ".join(part for part in (*names, outcome, reason) if part)


def _label(span: RecordedSpan) -> str:
    attempt = span.attributes.get("factory.stage.attempt")
    return f"{span.name} attempt {attempt}" if attempt is not None else span.name
