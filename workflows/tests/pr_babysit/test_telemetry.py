from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from workflows.tests.pr_babysit.support import Remote, Runner, execute


def test_babysit_roots_are_independent_and_polls_are_not_exported(scenario):
    settings, bare, checkout, base = scenario
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    class Observer:
        def span(self, name, attributes=None, *, role=None):
            return provider.get_tracer("test").start_as_current_span(name, attributes=attributes)
    observer = Observer()
    for _ in range(2):
        remote = Remote(bare, base, reviews=False)
        assert execute(settings, remote, Runner(), observer=observer) == 0
    spans = exporter.get_finished_spans()
    roots = [span for span in spans if span.name == "babysit"]
    assert len(roots) == 2
    assert all(span.parent is None for span in roots)
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert {span.name for span in spans} == {"babysit", "PR readiness changed"}
    provider.shutdown()
