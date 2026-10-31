"""Owned OpenTelemetry exporters end with their observer, not with the process."""

from __future__ import annotations

from threading import enumerate as threads
from unittest.mock import Mock

import pytest

pytest.importorskip("opentelemetry.sdk")

from loop.observe import otel  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import (  # noqa: E402
    MetricExporter,
    MetricExportResult,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON  # noqa: E402


class Metrics(MetricExporter):
    """Accepts metric exports in memory without making network requests."""

    def export(self, metrics_data, timeout_millis=10000, **kwargs):
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10000):
        return True

    def shutdown(self, timeout_millis=30000, **kwargs):
        return None


@pytest.fixture
def owned(monkeypatch):
    """Real SDK providers with background workers and observable lifecycle calls."""
    created = []

    def trace_provider(resource, endpoint):
        provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
        provider.add_span_processor(
            BatchSpanProcessor(InMemorySpanExporter(), schedule_delay_millis=60000)
        )
        return track(provider)

    def meter_provider(resource, endpoint):
        return track(
            MeterProvider(
                metric_readers=[
                    PeriodicExportingMetricReader(
                        Metrics(), export_interval_millis=60000
                    )
                ]
            )
        )

    def track(provider):
        provider.force_flush = Mock(wraps=provider.force_flush)
        provider.shutdown = Mock(wraps=provider.shutdown)
        created.append(provider)
        return provider

    monkeypatch.setattr(otel, "_own_tracer_provider", trace_provider)
    monkeypatch.setattr(otel, "_own_meter_provider", meter_provider)
    yield created
    for provider in created:
        if not provider.shutdown.called:
            provider.shutdown()


def test_repeated_observers_flush_and_join_all_owned_exporter_threads(owned):
    """Base, metric and lazy role providers close exactly once on every PR run."""
    before = set(threads())
    for _ in range(3):
        observer = otel.otlp_observer(resource_per_agent=True)
        with observer.span("run", role="engineer"):
            with observer.span("action", role="code"):
                pass
        background = set(threads()) - before
        assert background
        observer.close()
        observer.close()
        assert all(not thread.is_alive() for thread in background)
    assert len(owned) == 12
    for provider in owned:
        provider.force_flush.assert_called_once()
        provider.shutdown.assert_called_once()


def test_supplied_providers_remain_open_while_owned_role_providers_close(owned):
    """Closing an observer releases its own resources and preserves caller ownership."""
    tracer, meter = TracerProvider(sampler=ALWAYS_ON), MeterProvider()
    tracer.shutdown = Mock(wraps=tracer.shutdown)
    meter.shutdown = Mock(wraps=meter.shutdown)
    exporter = InMemorySpanExporter()
    tracer.add_span_processor(BatchSpanProcessor(exporter))
    try:
        observer = otel.otlp_observer(
            tracer_provider=tracer,
            meter_provider=meter,
            resource_per_agent=True,
        )
        with observer.span("role", role="engineer"):
            pass
        observer.close()
        tracer.shutdown.assert_not_called()
        meter.shutdown.assert_not_called()
        assert len(owned) == 1
        owned[0].shutdown.assert_called_once()
        with tracer.get_tracer("test").start_as_current_span("still usable"):
            pass
        tracer.force_flush()
        assert [span.name for span in exporter.get_finished_spans()] == ["still usable"]
    finally:
        tracer.shutdown()
        meter.shutdown()


def test_provider_construction_failure_closes_already_created_resources(
    owned, monkeypatch
):
    """A partially constructed observer does not leak its first provider."""

    def fail(*args):
        raise RuntimeError("metric setup failed")

    monkeypatch.setattr(otel, "_own_meter_provider", fail)
    with pytest.raises(RuntimeError, match="metric setup failed"):
        otel.otlp_observer()
    assert len(owned) == 1
    owned[0].shutdown.assert_called_once()


def test_a_flush_failure_still_shuts_down_every_owned_provider(owned):
    """One failing flush does not skip cleanup of the remaining exporters."""
    observer = otel.otlp_observer(resource_per_agent=True)
    with observer.span("run", role="engineer"):
        pass
    owned[-1].force_flush.side_effect = RuntimeError("flush failed")
    with pytest.raises(RuntimeError, match="flush failed"):
        observer.close()
    for provider in owned:
        provider.shutdown.assert_called_once()
