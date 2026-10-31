"""The span-capable observer the Workflow slices open their own spans on, and the factory span seam over it."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from loop import RunObserver, otlp_observer


class TracingObserver(RunObserver, Protocol):
    """A RunObserver that also opens Workflow-owned spans on its own trace provider."""

    def span(
        self,
        name: str,
        attributes: Mapping[str, str | int | bool] | None = None,
        *,
        role: str | None = None,
    ) -> AbstractContextManager[object]: ...


ObserverFactory = Callable[[str], TracingObserver]

LOGGER = logging.getLogger("workflows.software_factory.infrastructure.telemetry")

type Attributes = Mapping[str, str | int | bool]


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
            return otlp_observer(
                endpoint=endpoint,
                service_name=self._service_name,
                resource_per_agent=True,
            )
        return otlp_observer(endpoint=endpoint, service_name=self._service_name)


class FactorySpan(Protocol):
    """One open factory span; satisfied structurally by the runtime's span handle."""

    def set(self, **attributes: str | int | bool) -> None: ...

    def event(self, name: str, **attributes: str | int | bool) -> None: ...

    def fail(self, reason: str) -> None: ...


class FactorySpans(Protocol):
    """Opens factory spans; satisfied structurally by the runtime's `Spans`, which shared code never imports."""

    def open(
        self, name: str, attributes: Attributes, *, role: str
    ) -> AbstractContextManager[FactorySpan]: ...


class _OtelSpan:
    """Adapts the OpenTelemetry span an observer yields to the factory span handle."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set(self, **attributes: str | int | bool) -> None:
        for key, value in attributes.items():
            self._span.set_attribute(key, value)

    def event(self, name: str, **attributes: str | int | bool) -> None:
        self._span.add_event(name, attributes)

    def fail(self, reason: str) -> None:
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR, reason))


class _Detached:
    """The handle of a span that could not be opened."""

    def set(self, **attributes: str | int | bool) -> None:
        return None

    def event(self, name: str, **attributes: str | int | bool) -> None:
        return None

    def fail(self, reason: str) -> None:
        return None


class _Isolated:
    """A span handle whose failures are logged, never raised, so observation cannot change a run's outcome."""

    def __init__(self, inner: FactorySpan) -> None:
        self._inner = inner

    def set(self, **attributes: str | int | bool) -> None:
        self._guard("set attributes", lambda: self._inner.set(**attributes))

    def event(self, name: str, **attributes: str | int | bool) -> None:
        self._guard(f"add event {name}", lambda: self._inner.event(name, **attributes))

    def fail(self, reason: str) -> None:
        self._guard("set error status", lambda: self._inner.fail(reason))

    def _guard(self, action: str, call: Callable[[], None]) -> None:
        try:
            call()
        except Exception as exception:
            _report(action, exception)


def _report(action: str, exception: Exception) -> None:
    LOGGER.warning(
        "telemetry: could not %s: %s: %s", action, type(exception).__name__, exception
    )


@contextmanager
def _isolated[S](
    start: Callable[[], AbstractContextManager[S]], adapt: Callable[[S], FactorySpan]
) -> Iterator[FactorySpan]:
    """Runs a span's lifecycle without letting an observer failure escape; an exception from the body still propagates."""
    manager: AbstractContextManager[S] | None = None
    try:
        started = start()
        span = started.__enter__()
        manager = started
        handle: FactorySpan = _Isolated(adapt(span))
    except Exception as exception:
        _report("open span", exception)
        handle = _Detached()
    try:
        yield handle
    except BaseException as exception:
        _close(manager, exception)
        raise
    _close(manager, None)


def _close(
    manager: AbstractContextManager[Any] | None, error: BaseException | None
) -> None:
    if manager is None:
        return
    try:
        if error is None:
            manager.__exit__(None, None, None)
        else:
            manager.__exit__(type(error), error, error.__traceback__)
    except Exception as exception:
        _report("end span", exception)


class OtelSpans:
    """Opens the factory's spans on a tracing observer, so Agent Run, tool and Worktree spans nest under the current one.

    `opentelemetry` is imported only when a span is marked failed, behind the `loop[otel]` extra.
    """

    def __init__(self, observer: TracingObserver) -> None:
        self._observer = observer

    def open(
        self, name: str, attributes: Attributes, *, role: str
    ) -> AbstractContextManager[FactorySpan]:
        """Opens a span as resource `role`; a failing observer yields a detached span instead of raising."""
        return _isolated(
            lambda: self._observer.span(name, attributes, role=role), _OtelSpan
        )


class _TeeSpan:
    def __init__(self, spans: list[FactorySpan]) -> None:
        self._spans = spans

    def set(self, **attributes: str | int | bool) -> None:
        for span in self._spans:
            span.set(**attributes)

    def event(self, name: str, **attributes: str | int | bool) -> None:
        for span in self._spans:
            span.event(name, **attributes)

    def fail(self, reason: str) -> None:
        for span in self._spans:
            span.fail(reason)


class Tee:
    """Sends every span call to each of its span implementations, such as OtelSpans and the console log."""

    def __init__(self, *spans: FactorySpans) -> None:
        self._spans = spans

    @contextmanager
    def open(
        self, name: str, attributes: Attributes, *, role: str
    ) -> Iterator[FactorySpan]:
        """Opens the span on every implementation; one that fails is skipped."""
        with ExitStack() as stack:
            handles = [
                stack.enter_context(
                    _isolated(
                        lambda spans=spans: spans.open(name, attributes, role=role),
                        _identity,
                    )
                )
                for spans in self._spans
            ]
            yield _TeeSpan(handles)


def _identity(span: FactorySpan) -> FactorySpan:
    return span
