"""Babysit's OpenTelemetry export: every run is its own trace, and a poll that did nothing is not in it."""

from __future__ import annotations

from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from software_factory.shared.telemetry import OtelSpans
from factory_workflows.pr_babysit.__main__ import Runtime

from .harness import Babysit


class Observer:
    """The tracing observer over an in-memory exporter."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(sampler=ALWAYS_ON)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    def span(self, name, attributes=None, *, role=None):
        return self.provider.get_tracer("test").start_as_current_span(name, attributes=attributes)


def test_babysit_roots_are_independent_traces_and_quiet_polls_are_not_exported(tmp_path: Path) -> None:
    """Given two runs over a green pull request polled five times each, when they are exported, then each run is its own root trace and no quiet poll is among the spans."""
    observer = Observer()
    for name in ("first", "second"):
        rig = Babysit(tmp_path / name)
        rig.close_after(5)
        rig.services = rig.services.replace(Runtime(rig.ledger, OtelSpans(observer), rig.stop))
        assert rig.babysit() == 0

    spans = observer.exporter.get_finished_spans()
    roots = [span for span in spans if span.name == "pr_babysit"]
    names = {span.name for span in spans}
    assert len(roots) == 2 and all(span.parent is None for span in roots)
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert "report-readiness" in names and "poll poll-1" in names
    assert not names & {"poll poll-2", "poll poll-3", "poll poll-4"}
    observer.provider.shutdown()
