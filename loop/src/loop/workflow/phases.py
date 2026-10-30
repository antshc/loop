from __future__ import annotations

import logging
from asyncio import CancelledError as AsyncCancelledError
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import uuid4


logger = logging.getLogger("loop.workflow")


class Kind:
    """Distinguishes engineer, deterministic code, and agent phases."""

    ENGINEER = "engineer"
    CODE = "code"
    AGENT = "agent"


@dataclass
class Phase:
    """Records one phase occurrence and its explicit business outcome."""

    name: str
    kind: str
    owner: str
    attributes: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    visible: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)
    parent_id: str | None = None
    status: str = "running"
    reason: str | None = None
    seconds: float = 0.0
    gates: list[tuple[str, bool, str]] = field(default_factory=list)

    def set(self, **attributes: Any) -> None:
        """Adds business results without interpreting them as success."""
        self.attributes.update(attributes)

    def gate(self, check: str, ok: bool, note: str = "") -> bool:
        """Records a claim and fails the phase when it does not hold."""
        self.gates.append((check, ok, note))
        if not ok:
            self.fail(note or check)
        return ok

    def fail(self, reason: str) -> None:
        """Marks failure without allowing later success to erase it."""
        if self.status != "failed":
            self.reason = reason
        self.status = "failed"

    def succeed(self) -> None:
        """Completes a running phase without overriding another outcome."""
        if self.status == "running":
            self.status = "completed"

    def skip(self, reason: str) -> None:
        """Marks an unnecessary phase skipped without clearing failure."""
        if self.status == "running":
            self.status, self.reason = "skipped", reason


class Phases:
    """Tracks phases directly without a builder or output dependencies."""

    def __init__(self) -> None:
        self._parent: ContextVar[Phase | None] = ContextVar("loop_phase", default=None)
        self._executions: list[Phase] = []
        self._reporters: tuple[Any, ...] = ()
        self._telemetry: Any = None

    @property
    def executions(self) -> tuple[Phase, ...]:
        """Returns the phase occurrences in start order."""
        return tuple(self._executions)

    def phase(self, name: str, kind: str, owner: str, **attributes: Any) -> AbstractContextManager[Phase]:
        """Opens a phase with an owner and caller-supplied attributes."""
        return self._open(name, kind, owner, attributes, label=name)

    @contextmanager
    def _open(
        self, name: str, kind: str, owner: str, attributes: dict[str, Any],
        *, label: str, visible: bool = True,
    ) -> Iterator[Phase]:
        parent = self._parent.get()
        inherited = dict(parent.attributes) if parent is not None else {}
        inherited.update(attributes)
        phase = Phase(name, kind, owner, inherited, label, visible,
                      parent_id=parent.id if parent is not None else None)
        self._executions.append(phase)
        token = self._parent.set(phase)
        started = monotonic()
        scope = self._telemetry.open(phase) if self._telemetry else None
        self._notify("started", phase)
        try:
            yield phase
        except BaseException as exception:
            if isinstance(exception, (KeyboardInterrupt, GeneratorExit, AsyncCancelledError, CancelledError)):
                phase.status, phase.reason = "cancelled", type(exception).__name__
            else:
                phase.fail(type(exception).__name__)
            raise
        else:
            phase.succeed()
        finally:
            phase.seconds = monotonic() - started
            try:
                self._notify("finished", phase)
                if self._telemetry:
                    self._telemetry.close(scope, phase)
            finally:
                self._parent.reset(token)

    def _notify(self, point: str, phase: Phase) -> None:
        for reporter in self._reporters:
            try:
                reporter.emit(point, phase)
            except Exception as exception:
                # Reporting is observational and must not change execution.
                _reporting_failed("reporting", exception)


def _reporting_failed(operation: str, exception: Exception) -> None:
    try:
        logger.warning("Workflow %s unavailable (%s)", operation, type(exception).__name__)
    except Exception:
        # A broken host logging handler cannot become a workflow failure either.
        pass
