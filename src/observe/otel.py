from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter, MeterProvider
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    TracerProvider,
)

from .roles import Role
from .values import AgentRunRecord, ToolCall, Usage, WorktreeEvent

SCOPE = "loop"
DEFAULT_SERVICE_NAME = "loop"
_PROVIDERS = {"copilot": "github.copilot", "codex": "openai"}


def _nanos(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _seconds(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds()


class OtlpObserver:
    """Turns Agent Runs and Worktrees into OpenTelemetry spans and metrics; spans nest under the caller's current span."""

    def __init__(
        self,
        *,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
        capture_content: bool = False,
        resource_providers: Callable[[str], TracerProvider] | None = None,
    ) -> None:
        self._tracer = (tracer_provider or trace.get_tracer_provider()).get_tracer(
            SCOPE
        )
        self._resource_providers = resource_providers
        meter = (meter_provider or metrics.get_meter_provider()).get_meter(SCOPE)
        self._capture_content = capture_content
        self._worktrees: dict[Path, Span] = {}
        self._create_instruments(meter)

    def agent_run(self, record: AgentRunRecord) -> None:
        """Exports one `invoke_agent` span, its `execute_tool` children and the usage metrics."""
        tracer = self._resource_tracer(record.agent)
        span = tracer.start_span(
            f"invoke_agent {record.agent or record.cli}",
            context=self._parent_context(record.cwd),
            start_time=_nanos(record.started_at),
            attributes=self._agent_attributes(record),
        )
        for tool in record.tools:
            self._export_tool(tracer, span, tool)
        failed = record.error is not None or record.exit_code != 0
        if failed:
            span.set_status(
                Status(
                    StatusCode.ERROR, record.error or f"exit code {record.exit_code}"
                )
            )
        span.end(end_time=_nanos(record.ended_at))
        self._record_metrics(record, failed)

    def worktree(self, event: WorktreeEvent) -> None:
        """Holds a worktree span until removal or retention ends the active run."""
        if event.point == "ready":
            self._worktrees[event.path] = self._resource_tracer(Role.CODE).start_span(
                "loop.worktree",
                start_time=_nanos(event.at),
                attributes={"loop.worktree.path": str(event.path)},
            )
            return
        span = self._worktrees.get(event.path)
        if span is None or event.point not in {"removed", "retained"}:
            return
        del self._worktrees[event.path]
        if event.point == "retained":
            span.set_attribute("loop.worktree.retained", True)
        if event.ok is False:
            span.set_status(Status(StatusCode.ERROR, "worktree run failed"))
        span.end(end_time=_nanos(event.at))

    def span(
        self,
        name: str,
        attributes: Mapping[str, str | int | bool] | None = None,
        *,
        role: str | None = None,
    ) -> AbstractContextManager[Span]:
        """A Workflow-owned span, exported as resource `role` when given; Agent Run and Worktree spans started inside nest under it."""
        return self._resource_tracer(role).start_as_current_span(
            name, attributes=dict(attributes or {})
        )

    def _resource_tracer(self, resource: str | None) -> Tracer:
        """The tracer exporting as its own resource when the observer has resource providers, else the shared one."""
        if resource is None or self._resource_providers is None:
            return self._tracer
        return self._resource_providers(resource).get_tracer(SCOPE)

    def _parent_context(self, cwd: Path) -> otel_context.Context | None:
        # Explicit workflow phases take precedence over the worktree fallback.
        if trace.get_current_span().get_span_context().is_valid:
            return None
        span = self._worktrees.get(cwd)
        return trace.set_span_in_context(span) if span is not None else None

    def _agent_attributes(self, record: AgentRunRecord) -> dict[str, object]:
        attributes: dict[str, object] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": _PROVIDERS.get(record.cli, record.cli),
            "gen_ai.conversation.id": record.session,
            "loop.agent.cwd": str(record.cwd),
        }
        optional = {
            "gen_ai.agent.name": record.agent,
            "gen_ai.request.model": record.model,
            "loop.agent.thinking": record.reasoning_effort,
            "loop.agent.native_session_id": record.native_id,
            "loop.agent.exit_code": record.exit_code,
        }
        attributes.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        if record.usage is not None:
            attributes.update(_usage_attributes(record.usage))
        if self._capture_content:
            attributes.update(_content_attributes(record))
        return attributes

    def _export_tool(self, tracer: Tracer, agent_span: Span, tool: ToolCall) -> None:
        attributes: dict[str, object] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool.name,
            "gen_ai.tool.call.id": tool.call_id,
            "loop.tool.ok": tool.ok,
        }
        if self._capture_content:
            attributes["gen_ai.tool.call.arguments"] = json.dumps(dict(tool.arguments))
            attributes["gen_ai.tool.call.result"] = tool.result
        span = tracer.start_span(
            f"execute_tool {tool.name}",
            context=trace.set_span_in_context(agent_span),
            kind=SpanKind.INTERNAL,
            start_time=_nanos(tool.started_at),
            attributes=attributes,
        )
        span.set_status(Status(StatusCode.OK if tool.ok else StatusCode.ERROR))
        span.end(end_time=_nanos(tool.ended_at))

    def _create_instruments(self, meter: Meter) -> None:
        self._token_usage = meter.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Tokens used per agent invocation, by token type.",
        )
        self._operation_duration = meter.create_histogram(
            "gen_ai.client.operation.duration",
            unit="s",
            description="Duration of an agent invocation.",
        )
        self._aiu = meter.create_counter(
            "loop.agent.aiu",
            unit="{AIU}",
            description="GitHub AI units the agent consumed.",
        )
        self._premium_requests = meter.create_counter(
            "loop.agent.premium_requests",
            unit="{request}",
            description="Premium requests the user's prompts billed.",
        )

    def _record_metrics(self, record: AgentRunRecord, failed: bool) -> None:
        shared: dict[str, object] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": _PROVIDERS.get(record.cli, record.cli),
        }
        if record.model is not None:
            shared["gen_ai.request.model"] = record.model
        duration_attributes = (
            {**shared, "error.type": "agent_failed"} if failed else shared
        )
        self._operation_duration.record(
            _seconds(record.started_at, record.ended_at), duration_attributes
        )
        usage = record.usage
        if usage is None:
            return
        self._token_usage.record(
            usage.total_input_tokens, {**shared, "gen_ai.token.type": "input"}
        )
        self._token_usage.record(
            usage.output_tokens, {**shared, "gen_ai.token.type": "output"}
        )
        self._aiu.add(usage.total_aiu, shared)
        self._premium_requests.add(usage.premium_requests, shared)


def _usage_attributes(usage: Usage) -> dict[str, object]:
    attributes: dict[str, object] = {
        "gen_ai.usage.input_tokens": usage.total_input_tokens,
        "gen_ai.usage.output_tokens": usage.output_tokens,
        "loop.usage.input_tokens": usage.input_tokens,
        "loop.usage.output_tokens": usage.output_tokens,
        "loop.usage.reasoning_tokens": usage.reasoning_tokens,
        "loop.usage.cache_read_tokens": usage.cache_read_tokens,
        "loop.usage.cache_write_tokens": usage.cache_write_tokens,
        "loop.usage.total_tokens": usage.total_input_tokens + usage.output_tokens,
        "loop.usage.total_aiu": usage.total_aiu,
        "loop.usage.premium_requests": usage.premium_requests,
        "loop.agent.api_calls": usage.api_calls,
    }
    attributes.update(
        {f"loop.usage.{kind}_aiu": value for kind, value in usage.aiu.items()}
    )
    return attributes


def _content_attributes(record: AgentRunRecord) -> dict[str, object]:
    attributes: dict[str, object] = {
        "gen_ai.input.messages": json.dumps(
            [{"role": "user", "parts": [{"type": "text", "content": record.prompt}]}]
        )
    }
    if record.output is not None:
        attributes["gen_ai.output.messages"] = json.dumps(
            [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": record.output}],
                }
            ]
        )
    return attributes


def otlp_observer(
    *,
    endpoint: str | None = None,
    service_name: str | None = None,
    capture_content: bool = False,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
    resource_per_agent: bool = False,
) -> OtlpObserver:
    """An observer exporting over OTLP/HTTP; `endpoint` is a base URL such as http://localhost:4318 (the standard `OTEL_EXPORTER_OTLP_*` variables apply when it is None).

    With `resource_per_agent`, each named agent, each `span(role=...)` and the Worktree (as `Role.CODE`) export as their own resource, which backends such as Aspire color apart.
    """
    name = service_name or os.environ.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE_NAME
    # Resource-per-agent backends list each resource by name, so drop the per-process suffix there too.
    resource = Resource.create(
        {
            "service.name": name,
            **({"service.instance.id": name} if resource_per_agent else {}),
        }
    )
    return OtlpObserver(
        tracer_provider=tracer_provider or _own_tracer_provider(resource, endpoint),
        meter_provider=meter_provider or _own_meter_provider(resource, endpoint),
        capture_content=capture_content,
        resource_providers=_resource_providers(endpoint)
        if resource_per_agent
        else None,
    )


def _resource_providers(endpoint: str | None) -> Callable[[str], TracerProvider]:
    providers: dict[str, TracerProvider] = {}

    def provider(name: str) -> TracerProvider:
        if name not in providers:
            # A fixed instance id keeps Aspire from suffixing the resource name with a per-process id.
            resource = Resource.create(
                {"service.name": name, "service.instance.id": name}
            )
            providers[name] = _own_tracer_provider(resource, endpoint)
        return providers[name]

    return provider


def _signal_endpoint(endpoint: str | None, signal: str) -> str | None:
    return None if endpoint is None else f"{endpoint.rstrip('/')}/v1/{signal}"


def _own_tracer_provider(resource: Resource, endpoint: str | None) -> SdkTracerProvider:
    provider = SdkTracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=_signal_endpoint(endpoint, "traces"))
        )
    )
    return provider


def _own_meter_provider(resource: Resource, endpoint: str | None) -> SdkMeterProvider:
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=_signal_endpoint(endpoint, "metrics"))
    )
    return SdkMeterProvider(resource=resource, metric_readers=[reader])
