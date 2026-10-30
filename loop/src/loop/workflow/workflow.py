from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TextIO

from .phases import Kind, Phase, Phases

if TYPE_CHECKING:
    from .builder import PhaseDefinition


@dataclass(frozen=True)
class Workflow:
    """An immutable phase definition that creates independent runs."""

    name: str
    definitions: tuple[PhaseDefinition, ...]
    _console: bool = False
    _visualize: bool = False
    _stream: TextIO | None = None
    _observer: Any = None

    @contextmanager
    def run(self, **attributes: Any) -> Iterator[WorkflowRun]:
        """Opens one run whose business outcome is controlled by its caller."""
        run = WorkflowRun(self)
        with run._open(self.name, Kind.CODE, "code", attributes, label=self.name) as root:
            run._root = root
            run._active = True
            try:
                yield run
            finally:
                run._active = False


class WorkflowRun(Phases):
    """Executes declared phase scopes without scheduling their Python bodies."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__()
        self._definitions = {definition.name: definition for definition in workflow.definitions}
        self._active = False
        self._root: Phase | None = None
        reporters = []
        if workflow._console:
            from .adapters.console import ConsoleReporter
            reporters.append(ConsoleReporter())
        if workflow._visualize:
            from .adapters.terminal import TerminalReporter
            reporters.append(TerminalReporter(workflow.name, workflow.definitions, workflow._stream))
        self._reporters = tuple(reporters)
        if workflow._observer is not None:
            from .adapters.otel import OtelReporter
            self._telemetry = OtelReporter(workflow._observer)

    @property
    def outcome(self) -> Phase:
        """Exposes the root result, including duration and business attributes."""
        if self._root is None:
            raise RuntimeError("Workflow run has not started")
        return self._root

    def phase(
        self, name: str, kind: str | None = None, owner: str | None = None,
        **attributes: Any,
    ) -> AbstractContextManager[Phase]:
        """Opens a declared phase with fresh occurrence and attempt identity."""
        if not self._active:
            raise RuntimeError("Workflow run is closed")
        if name not in self._definitions:
            raise ValueError(f"Unknown phase: {name}")
        definition = self._definitions[name]
        if kind is not None and kind != definition.kind:
            raise ValueError(f"Phase {name!r} kind differs from its declaration")
        if owner is not None and owner != definition.owner:
            raise ValueError(f"Phase {name!r} owner differs from its declaration")
        return self._declared_phase(definition, attributes)

    @contextmanager
    def _declared_phase(self, definition: PhaseDefinition, attributes: dict[str, Any]) -> Iterator[Phase]:
        # Recheck at entry: callers can retain a context manager after the run ends.
        if not self._active:
            raise RuntimeError("Workflow run is closed")
        with self._open(definition.name, definition.kind, definition.owner, attributes,
                        label=definition.label, visible=definition.visible) as phase:
            yield phase

    def fail(self, reason: str) -> None:
        """Fails the run when the caller decides recovery was unsuccessful."""
        self.outcome.fail(reason)

    def set(self, **attributes: Any) -> None:
        """Records final business results such as verdict or PR readiness."""
        self.outcome.set(**attributes)

    def skip(self, name: str, reason: str, **attributes: Any) -> None:
        """Records a declared phase as skipped without running its body."""
        with self.phase(name, **attributes) as phase:
            phase.skip(reason)
