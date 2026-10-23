"""The span-capable observer accepted by the dev composition root."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol

from loop import RunObserver

class TracingObserver(RunObserver, Protocol):
    """A RunObserver that also opens Workflow-owned spans on its own trace provider."""

    def span(
        self, name: str, attributes: Mapping[str, str | int | bool] | None = None, *, role: str | None = None
    ) -> AbstractContextManager[object]: ...
