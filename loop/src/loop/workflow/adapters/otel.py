from __future__ import annotations

from typing import Any

from ..phases import Phase, _reporting_failed


class OtelReporter:
    """Keeps observer spans current through each phase and exports its final facts."""

    def __init__(self, observer: Any) -> None:
        self._observer = observer

    def open(self, phase: Phase) -> Any:
        try:
            attributes = self._attributes(phase)
            scope = self._observer.span(phase.label, attributes, role=phase.owner)
            return scope, scope.__enter__()
        except Exception as exception:
            _reporting_failed("telemetry", exception)
            return None

    def close(self, scope: Any, phase: Phase) -> None:
        if scope is None:
            return
        manager, span = scope
        try:
            from opentelemetry.trace import Status, StatusCode
            for key, value in self._attributes(phase).items():
                span.set_attribute(key, value)
            span.set_attribute("loop.phase.status", phase.status)
            for check, ok, note in phase.gates:
                span.add_event("loop.gate", {"loop.gate.check": check, "loop.gate.ok": ok, "loop.gate.note": note})
            if phase.status in ("failed", "cancelled"):
                span.set_status(Status(StatusCode.ERROR, phase.reason or phase.status))
        except Exception as exception:
            _reporting_failed("telemetry", exception)
        finally:
            try:
                manager.__exit__(None, None, None)
            except Exception as exception:
                _reporting_failed("telemetry cleanup", exception)

    def _attributes(self, phase: Phase) -> dict[str, Any]:
        attributes = {
            f"loop.{key}": value if isinstance(value, (str, int, float, bool)) else str(value)
            for key, value in phase.attributes.items() if value is not None
        }
        attributes.update({"loop.phase.id": phase.id, "loop.phase.kind": phase.kind,
                           "loop.phase.owner": phase.owner})
        if phase.parent_id is not None:
            attributes["loop.phase.parent_id"] = phase.parent_id
        return attributes
