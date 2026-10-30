from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TextIO

from .workflow import Workflow


@dataclass(frozen=True)
class PhaseDefinition:
    """Declares the presentation and ownership of a named phase."""

    name: str
    label: str
    kind: str
    owner: str
    visible: bool = True


class WorkflowBuilder:
    """Builds an optional reporting definition for ordinary Python workflows."""

    def __init__(self, name: str) -> None:
        if not name.strip():
            raise ValueError("Workflow name must not be empty")
        self._name = name
        self._definitions: dict[str, PhaseDefinition] = {}
        self._console = False
        self._visualize = False
        self._stream: TextIO | None = None
        self._observer: Any = None

    def phase(
        self, name: str, label: str, kind: str, owner: str, *, visible: bool = True,
    ) -> WorkflowBuilder:
        """Declares a unique phase, optionally hidden from console and terminal."""
        if not all(value.strip() for value in (name, label, kind, owner)):
            raise ValueError("Phase name, label, kind, and owner must not be empty")
        if name in self._definitions:
            raise ValueError(f"Duplicate phase: {name}")
        self._definitions[name] = PhaseDefinition(name, label, kind, owner, visible)
        return self

    def console(self) -> WorkflowBuilder:
        """Enables high-level records through the host's logging configuration."""
        self._console = True
        return self

    def visualize(self, *, stream: TextIO | None = None) -> WorkflowBuilder:
        """Enables the optional Rich terminal view on stderr or the given stream."""
        self._visualize, self._stream = True, stream
        return self

    def otel(self, observer: Any) -> WorkflowBuilder:
        """Shares an existing span-capable observer with phase reporting."""
        if not callable(getattr(observer, "span", None)):
            raise TypeError("Workflow telemetry needs an observer with span()")
        self._observer = observer
        return self

    def build(self) -> Workflow:
        """Snapshots declarations without executing phases or configuring logging."""
        if self._visualize:
            from .adapters.terminal import require_rich
            require_rich()
        return Workflow(self._name, tuple(self._definitions.values()), self._console,
                        self._visualize, self._stream, self._observer)
