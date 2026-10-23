"""Workflow-owned spans for the optional OpenTelemetry observer; a no-op unless the run opts in."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from typing import Callable, Protocol

from loop import RunObserver

# Opens a named span with attributes; Agent Run and Worktree spans started inside it nest under it.
SpanFactory = Callable[[str, Mapping[str, "str | int | bool"]], AbstractContextManager[object]]


class TracingObserver(RunObserver, Protocol):
    """A RunObserver that also opens Workflow-owned spans on its own trace provider."""

    def span(self, name: str, attributes: Mapping[str, str | int | bool] | None = None) -> AbstractContextManager[object]: ...


def no_span(name: str, attributes: Mapping[str, str | int | bool]) -> AbstractContextManager[object]:
    """The default: opens nothing."""
    return nullcontext()
