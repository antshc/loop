"""The span-capable observer the Workflow slices open their own spans on."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from loop import RunObserver, otlp_observer


class TracingObserver(RunObserver, Protocol):
    """A RunObserver that also opens Workflow-owned spans on its own trace provider."""

    def span(
        self, name: str, attributes: Mapping[str, str | int | bool] | None = None, *, role: str | None = None
    ) -> AbstractContextManager[object]: ...


ObserverFactory = Callable[[str], TracingObserver]


@dataclass(frozen=True)
class Telemetry:
    """The process's span observer; `observer` is None when telemetry is off."""

    observer: TracingObserver | None = None


class OtlpObserverFactory:
    """Builds the OTLP observer of one service for an endpoint."""

    def __init__(self, service_name: str, *, resource_per_agent: bool = False) -> None:
        self._service_name = service_name
        self._resource_per_agent = resource_per_agent

    def __call__(self, endpoint: str) -> TracingObserver:
        if self._resource_per_agent:
            return otlp_observer(endpoint=endpoint, service_name=self._service_name, resource_per_agent=True)
        return otlp_observer(endpoint=endpoint, service_name=self._service_name)
