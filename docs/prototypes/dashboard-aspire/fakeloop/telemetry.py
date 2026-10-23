"""OTLP export to the Aspire dashboard: spans for what finished, logs for what just started, metrics."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Span
from opentelemetry.util.types import Attributes

# The session span and the logs and metrics export as this resource; each phase role gets its own
# resource (`developer`, ...) because Aspire colors spans by resource.
SERVICE_NAME = "workflow"
SCOPE = "loop.fakeloop"


def _resource(name: str) -> Resource:
    # A fixed instance id keeps Aspire from suffixing the name with a per-process id.
    return Resource.create({"service.name": name, "service.instance.id": name})


def span_hex(span: Span) -> str:
    """The span id in the 16-hex form OTLP JSON uses."""
    return format(span.get_span_context().span_id, "016x")


def trace_hex(span: Span) -> str:
    """The trace id in the 32-hex form OTLP JSON uses."""
    return format(span.get_span_context().trace_id, "032x")


class Telemetry:
    """Owns the tracer, logger and meters that export to `OTEL_EXPORTER_OTLP_ENDPOINT` (default :4318)."""

    def __init__(self) -> None:
        resource = _resource(SERVICE_NAME)
        # Simple processors export each record as it completes, so a run can be watched live.
        self._tracer_providers = {SERVICE_NAME: self._new_tracer_provider(resource)}
        self._logger_provider = LoggerProvider(resource=resource)
        self._logger_provider.add_log_record_processor(
            SimpleLogRecordProcessor(OTLPLogExporter()))
        self._meter_provider = MeterProvider(
            resource=resource, metric_readers=[PeriodicExportingMetricReader(
                OTLPMetricExporter(), export_interval_millis=2000)])
        self.tracer = self._tracer_providers[SERVICE_NAME].get_tracer(SCOPE)
        self._logger = self._logger_provider.get_logger(SCOPE)
        meter = self._meter_provider.get_meter(SCOPE)
        self.token_usage = meter.create_histogram(
            "gen_ai.client.token.usage", unit="{token}",
            description="Tokens used per agent invocation, by token type.")
        self.operation_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s",
            description="Duration of an agent invocation.")
        self.cost = meter.create_counter(
            "loop.agent.cost", unit="USD", description="Estimated agent spend.")
        self.aiu = meter.create_counter(
            "loop.agent.aiu", unit="{AIU}", description="GitHub AI units the agent consumed.")
        self.premium_requests = meter.create_counter(
            "loop.agent.premium_requests", unit="{request}",
            description="Premium requests the user's prompts billed.")
        self.gate_results = meter.create_counter(
            "loop.gate.results", unit="{gate}", description="Gate verdicts by gate and outcome.")
        self.phase_duration = meter.create_histogram(
            "loop.phase.duration", unit="s", description="Duration of a phase, by outcome.")

    def tracer_for(self, role: str) -> trace.Tracer:
        """The tracer that exports spans as the `{role}` resource."""
        if role not in self._tracer_providers:
            self._tracer_providers[role] = self._new_tracer_provider(_resource(role))
        return self._tracer_providers[role].get_tracer(SCOPE)

    @staticmethod
    def _new_tracer_provider(resource: Resource) -> TracerProvider:
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
        return provider

    def started(self, span: Span, event: str, attributes: Attributes,
                parent: Span | None = None) -> None:
        """Logs that `span` opened; the span itself is exported only when it ends."""
        marker = {**(attributes or {}), "loop.event": event, "loop.span_id": span_hex(span)}
        if parent is not None:
            marker["loop.parent_span_id"] = span_hex(parent)
        self._logger.emit(context=trace.set_span_in_context(span),
                          severity_number=SeverityNumber.INFO, severity_text="Information",
                          body=event, event_name=event, attributes=marker)

    def shutdown(self) -> None:
        """Flushes and closes the exporters."""
        for provider in self._tracer_providers.values():
            provider.shutdown()
        self._logger_provider.shutdown()
        self._meter_provider.shutdown()
