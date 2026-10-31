"""Babysit's OpenTelemetry export: every run is its own trace, and a poll that did nothing is not in it."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from workflows.software_factory.infrastructure.telemetry import OtelSpans
from workflows.software_factory.workflows.pr_babysit.__main__ import Runtime

from .harness import Babysit
from .test_intake import Discovery, Workers


class Observer:
    """The tracing observer over an in-memory exporter."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(sampler=ALWAYS_ON)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    def span(self, name, attributes=None, *, role=None):
        return self.provider.get_tracer("test").start_as_current_span(
            name, attributes=attributes
        )


def test_babysit_roots_are_independent_traces_and_quiet_polls_are_not_exported(
    tmp_path: Path,
) -> None:
    """Given two runs over a green pull request polled five times each, when they are exported, then each run is its own root trace and no quiet poll is among the spans."""
    observer = Observer()
    for name in ("first", "second"):
        rig = Babysit(tmp_path / name)
        rig.close_after(5)
        rig.services = rig.services.replace(
            Runtime(rig.ledger, OtelSpans(observer), rig.stop)
        )
        assert rig.babysit() == 0

    spans = observer.exporter.get_finished_spans()
    roots = [span for span in spans if span.name == "pr_babysit"]
    names = {span.name for span in spans}
    assert len(roots) == 2 and all(span.parent is None for span in roots)
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert "report-readiness" in names and "poll poll-1" in names
    assert not names & {"poll poll-2", "poll poll-3", "poll poll-4"}
    observer.provider.shutdown()


def test_reused_worker_threads_start_with_an_empty_trace_and_context(
    tmp_path: Path, monkeypatch
) -> None:
    """Each submitted run starts a new root even inside an enclosing trace."""
    observer = Observer()
    leaked = ContextVar("leaked")
    discovery = Discovery(tmp_path, concurrency=1)
    discovery.host.add(2, head_sha="sha2")
    monkeypatch.setenv("TRACEPARENT", "external-parent")
    monkeypatch.setenv("baggage", "external-baggage")

    class TracedWorkers(Workers):
        def __call__(self, slug, number):
            assert leaked.get(None) is None
            leaked.set(number)
            with observer.span("pr_babysit"):
                return super().__call__(slug, number)

    discovery.workers = TracedWorkers(discovery.ledger)

    def finish_first(tick):
        if tick == 1:
            discovery.host.pulls[1].close()
            discovery.workers.runs[0].returncode = 0

    try:
        with observer.span("caller"):
            leaked.set("caller")
            discovery.serve(2, "--include-existing", on_tick=finish_first)
        spans = observer.exporter.get_finished_spans()
        roots = [span for span in spans if span.name == "pr_babysit"]
        assert len(roots) == 2 and all(span.parent is None for span in roots)
        assert len({span.context.trace_id for span in spans}) == 3
        assert leaked.get() == "caller"
        assert os.environ["TRACEPARENT"] == "external-parent"
        assert os.environ["baggage"] == "external-baggage"
    finally:
        observer.provider.shutdown()
