"""The phase primitive: a context manager that traces a phase as a span under the session span."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Protocol

from loop import Prompt
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from .model import AgentConfig, Attempt, Phase, PhaseParams
from .telemetry import Telemetry, trace_hex


class PhaseAgent(Protocol):
    """What a phase needs from an agent, fake or real."""

    def call(
        self,
        ph: PhaseHandle,
        prompt: Prompt,
        output_type: str,
        gate: str,
        attempts: Sequence[Attempt],
    ) -> dict[str, object]:
        """Runs the agent for the phase and returns its envelope."""
        ...


AgentFactory = Callable[[AgentConfig, str], PhaseAgent]


class PhaseHandle:
    """What the body of a `with run.phase(...)` block works with."""

    def __init__(self, run: Run, phase: Phase, span: Span) -> None:
        self.run = run
        self.phase = phase
        self.span = span

    @property
    def tracer(self) -> trace.Tracer:
        """The tracer for the phase role's resource, so the phase and its children share a color."""
        return self.run.telemetry.tracer_for(self.phase.params.role)

    def log(self, **payload: object) -> None:
        """Adds a `log` span event; an engineer phase's `input` also becomes the session request."""
        self.span.add_event("log", {key: str(value) for key, value in payload.items()})
        if self.phase.params.kind == "engineer" and "input" in payload:
            self.run.set_request(str(payload["input"]))


class Run:
    """One session: a root span whose trace id is the session id, and the phases entered so far."""

    def __init__(
        self,
        telemetry: Telemetry,
        engineer: str,
        adw_name: str,
        speed: float = 1.0,
        *,
        agents: AgentFactory,
    ) -> None:
        self.telemetry = telemetry
        self._agents = agents
        self.speed = speed
        self.phases: list[Phase] = []
        self._seq = 0
        attributes = {
            "loop.session.adw_name": adw_name,
            "loop.session.engineer": engineer,
        }
        self._root = telemetry.tracer.start_span(adw_name, attributes=attributes)
        self._parent = self._root
        self._ticket_id: int | None = None
        self._ticket_title: str | None = None
        self._spec_title: str | None = None
        self.adw_id = trace_hex(self._root)
        telemetry.started(self._root, "loop.session.start", attributes)

    def agent(self, config: AgentConfig, system_prompt: str) -> PhaseAgent:
        """A new agent for one phase, built by the factory this run was started with."""
        return self._agents(config, system_prompt)

    def sleep(self, seconds: float) -> None:
        """Pauses so the dashboard can be watched live in Aspire; `--speed 0` skips the wait."""
        time.sleep(seconds * self.speed)

    def set_spec(self, title: str) -> None:
        """Names the spec this run delivers in the session span name; later spans carry `loop.spec.title`."""
        self._spec_title = title
        self._root.update_name(f"{self._root.name}: {title}")
        self._root.set_attribute("loop.spec.title", title)

    def set_request(self, request: str) -> None:
        """Stores the engineer's request on the session span."""
        self._root.set_attribute("loop.session.request", request[:500])

    @contextmanager
    def ticket(self, ticket_id: int, title: str) -> Iterator[None]:
        """Groups the phases entered inside it under one ticket span."""
        attributes = {
            "loop.ticket.id": ticket_id,
            "loop.ticket.title": title,
            **self._spec_attributes(),
        }
        span = self.telemetry.tracer.start_span(
            f"#{ticket_id} {title}",
            context=trace.set_span_in_context(self._root),
            attributes=attributes,
        )
        self.telemetry.started(span, "loop.ticket.start", attributes, parent=self._root)
        self._parent, self._ticket_id, self._ticket_title = span, ticket_id, title
        try:
            yield
        except BaseException as exception:
            span.set_status(Status(StatusCode.ERROR, f"ticket #{ticket_id} failed"))
            span.record_exception(exception)
            raise
        else:
            span.set_status(Status(StatusCode.OK))
        finally:
            self._parent, self._ticket_id, self._ticket_title = self._root, None, None
            span.end()

    @contextmanager
    def phase(self, params: PhaseParams) -> Iterator[PhaseHandle]:
        """Runs one phase; success is earned by a clean exit, any exception marks it failed."""
        self._seq += 1
        phase = Phase(params=params)
        self.phases.append(phase)
        attributes = {
            "loop.phase.seq": self._seq,
            "loop.phase.name": params.name,
            "loop.phase.kind": params.kind,
            "loop.phase.owner": params.owner,
            "loop.phase.role": params.role,
            "loop.phase.description": params.description,
            "loop.phase.retries": params.retries,
        }
        attributes.update(self._spec_attributes())
        if self._ticket_id is not None:
            attributes["loop.ticket.id"] = self._ticket_id
            attributes["loop.ticket.title"] = self._ticket_title
        span = self.telemetry.tracer_for(params.role).start_span(
            params.name,
            context=trace.set_span_in_context(self._parent),
            attributes=attributes,
        )
        self.telemetry.started(
            span, "loop.phase.start", attributes, parent=self._parent
        )
        started_at = time.perf_counter()
        try:
            with trace.use_span(span, end_on_exit=False):
                yield PhaseHandle(self, phase, span)
        except BaseException as exception:
            phase.status = "fail"
            self._end(
                span, phase, Status(StatusCode.ERROR, str(exception)[:1000]), exception
            )
            self._root.set_status(Status(StatusCode.ERROR, f"{params.name} failed"))
            self._record_duration(params.name, phase.status, started_at)
            raise
        phase.status = "success"
        self._end(span, phase, Status(StatusCode.OK), None)
        self._record_duration(params.name, phase.status, started_at)

    def _spec_attributes(self) -> dict[str, str]:
        return {} if self._spec_title is None else {"loop.spec.title": self._spec_title}

    def _record_duration(self, name: str, status: str, started_at: float) -> None:
        self.telemetry.phase_duration.record(
            time.perf_counter() - started_at,
            {"loop.phase.name": name, "loop.phase.status": status},
        )

    def finish(self) -> int:
        """Settles and ends the session span from its phases and returns the exit code."""
        ok = bool(self.phases) and all(
            phase.status == "success" for phase in self.phases
        )
        if ok:
            self._root.set_status(Status(StatusCode.OK))
        self._root.end()
        return 0 if ok else 1

    @staticmethod
    def _end(
        span: Span, phase: Phase, status: Status, exception: BaseException | None
    ) -> None:
        span.set_attribute("loop.phase.attempt", phase.attempt)
        if exception is not None:
            span.record_exception(exception)
        span.set_status(status)
        span.end()
