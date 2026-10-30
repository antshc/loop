"""OTel adapter tests against OpenTelemetry's in-memory exporters; skipped when the `loop[otel]` extra is absent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from loop import AgentRunRecord, ToolCall, Usage, WorktreeEvent  # noqa: E402
from loop.observe.otel import OtlpObserver, otlp_observer  # noqa: E402

START = datetime(2026, 10, 23, 13, 0, 0, tzinfo=timezone.utc)
CWD = Path("/work/tree")


class Capture:
    def __init__(self, *, capture_content: bool = False) -> None:
        self.spans = InMemorySpanExporter()
        self.metrics = InMemoryMetricReader()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.tracer = tracer_provider.get_tracer("workflow")
        self.observer = OtlpObserver(
            tracer_provider=tracer_provider,
            meter_provider=MeterProvider(metric_readers=[self.metrics]),
            capture_content=capture_content,
        )

    def span(self, name: str):
        found = [span for span in self.spans.get_finished_spans() if span.name == name]
        assert len(found) == 1, f"{name}: {len(found)} spans"
        return found[0]

    def metric(self, name: str):
        data = self.metrics.get_metrics_data()
        found = [m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name == name]
        assert len(found) == 1, f"{name}: {len(found)} metrics"
        return found[0]


def record(*, usage: Usage | None = None, exit_code: int | None = 0, error: str | None = None) -> AgentRunRecord:
    tool = ToolCall("t1", "bash", {"command": "ls"}, "a.py", True, START + timedelta(seconds=1), START + timedelta(seconds=2))
    return AgentRunRecord(
        cli="copilot", session="ticket-7", native_id="n1", cwd=CWD, started_at=START, ended_at=START + timedelta(seconds=5),
        exit_code=exit_code, error=error, model="claude-haiku-4.5", reasoning_effort="medium",
        usage=usage, tools=(tool,) if usage else (), prompt="secret prompt", output="secret output",
    )


USAGE = Usage(
    api_calls=2, input_tokens=500, output_tokens=100, reasoning_tokens=10, cache_read_tokens=2_100,
    cache_write_tokens=400, aiu={"input": 0.1, "output": 0.1}, premium_requests=1.0,
)


def test_agent_span_carries_real_times_usage_and_tool_children() -> None:
    capture = Capture()

    capture.observer.agent_run(record(usage=USAGE))

    agent = capture.span("invoke_agent copilot")
    assert (agent.end_time - agent.start_time) / 1e9 == 5
    assert agent.attributes["gen_ai.conversation.id"] == "ticket-7"
    assert agent.attributes["gen_ai.usage.input_tokens"] == 3_000
    assert agent.attributes["loop.usage.input_tokens"] == 500
    assert agent.attributes["loop.usage.premium_requests"] == 1.0
    assert agent.attributes["loop.usage.input_aiu"] == 0.1
    tool = capture.span("execute_tool bash")
    assert tool.parent.span_id == agent.context.span_id
    assert (tool.end_time - tool.start_time) / 1e9 == 1


def test_content_is_exported_only_on_request() -> None:
    quiet, verbose = Capture(), Capture(capture_content=True)

    quiet.observer.agent_run(record(usage=USAGE))
    verbose.observer.agent_run(record(usage=USAGE))

    assert "gen_ai.input.messages" not in quiet.span("invoke_agent copilot").attributes
    assert "gen_ai.tool.call.arguments" not in quiet.span("execute_tool bash").attributes
    assert "secret prompt" in verbose.span("invoke_agent copilot").attributes["gen_ai.input.messages"]
    assert "secret output" in verbose.span("invoke_agent copilot").attributes["gen_ai.output.messages"]


def test_agent_span_nests_under_the_workflows_current_span() -> None:
    capture = Capture()

    with capture.tracer.start_as_current_span("#1 add basket") as ticket:
        capture.observer.agent_run(record(usage=USAGE))

    assert capture.span("invoke_agent copilot").parent.span_id == ticket.get_span_context().span_id


def test_worktree_span_spans_ready_to_removed_and_parents_the_agent_run() -> None:
    capture = Capture()

    capture.observer.worktree(WorktreeEvent("ready", CWD, START, None))
    capture.observer.agent_run(record(usage=USAGE))
    capture.observer.worktree(WorktreeEvent("removing", CWD, START + timedelta(seconds=6), True))
    capture.observer.worktree(WorktreeEvent("removed", CWD, START + timedelta(seconds=7), True))

    worktree = capture.span("loop.worktree")
    assert (worktree.end_time - worktree.start_time) / 1e9 == 7
    assert capture.span("invoke_agent copilot").parent.span_id == worktree.context.span_id
    assert worktree.status.status_code == trace.StatusCode.UNSET


def test_failed_worktree_and_failed_run_are_marked_error() -> None:
    capture = Capture()

    capture.observer.worktree(WorktreeEvent("ready", CWD, START, None))
    capture.observer.agent_run(record(exit_code=None, error="CalledProcessError(2)"))
    capture.observer.worktree(WorktreeEvent("removed", CWD, START + timedelta(seconds=7), False))

    assert capture.span("loop.worktree").status.status_code == trace.StatusCode.ERROR
    agent = capture.span("invoke_agent copilot")
    assert agent.status.status_code == trace.StatusCode.ERROR and "CalledProcessError" in agent.status.description


def test_unobservable_run_exports_a_span_without_usage_and_records_duration_only() -> None:
    capture = Capture()

    capture.observer.agent_run(record(usage=None))

    assert "gen_ai.usage.input_tokens" not in capture.span("invoke_agent copilot").attributes
    assert capture.metric("gen_ai.client.operation.duration").data.data_points[0].sum == 5


def test_usage_metrics_split_input_and_output_tokens() -> None:
    capture = Capture()

    capture.observer.agent_run(record(usage=USAGE))

    points = {p.attributes["gen_ai.token.type"]: p.sum for p in capture.metric("gen_ai.client.token.usage").data.data_points}
    assert points == {"input": 3_000, "output": 100}
    assert capture.metric("loop.agent.premium_requests").data.data_points[0].value == 1.0
    assert capture.metric("loop.agent.aiu").data.data_points[0].value == pytest.approx(0.2)


def test_workflow_span_from_the_observer_parents_agent_runs_on_the_same_provider() -> None:
    capture = Capture()

    with capture.observer.span("#1 add basket", {"loop.ticket.number": 1}):
        capture.observer.agent_run(record(usage=USAGE))

    ticket = capture.span("#1 add basket")
    assert ticket.attributes["loop.ticket.number"] == 1
    assert capture.span("invoke_agent copilot").parent.span_id == ticket.context.span_id


def test_otlp_observer_builds_with_an_explicit_endpoint() -> None:
    assert isinstance(otlp_observer(endpoint="http://localhost:4318/", service_name="dev"), OtlpObserver)


class ResourceCapture:
    """One in-memory exporter per resource name, to see which resource each span is exported as."""

    def __init__(self) -> None:
        self.exporters: dict[str, InMemorySpanExporter] = {}
        self.shared = InMemorySpanExporter()
        shared_provider = TracerProvider()
        shared_provider.add_span_processor(SimpleSpanProcessor(self.shared))
        self.tracer = shared_provider.get_tracer("workflow")
        self.observer = OtlpObserver(
            tracer_provider=shared_provider,
            meter_provider=MeterProvider(metric_readers=[InMemoryMetricReader()]),
            resource_providers=self._provider,
        )

    def _provider(self, name: str) -> TracerProvider:
        exporter = self.exporters.setdefault(name, InMemorySpanExporter())
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        return provider

    def names(self, resource: str) -> list[str]:
        return [span.name for span in self.exporters[resource].get_finished_spans()]


def named_record(agent: str | None) -> AgentRunRecord:
    return AgentRunRecord(**{**record(usage=USAGE).__dict__, "agent": agent})


def test_named_agents_and_the_worktree_export_as_their_own_resources() -> None:
    capture = ResourceCapture()

    with capture.tracer.start_as_current_span("#1 add basket"):
        capture.observer.worktree(WorktreeEvent("ready", CWD, START, None))
        capture.observer.agent_run(named_record("planner"))
        capture.observer.agent_run(named_record("developer"))
        capture.observer.worktree(WorktreeEvent("removed", CWD, START + timedelta(seconds=7), True))

    assert capture.names("planner") == ["execute_tool bash", "invoke_agent planner"]
    assert capture.names("developer") == ["execute_tool bash", "invoke_agent developer"]
    assert capture.names("code") == ["loop.worktree"]
    assert [span.name for span in capture.shared.get_finished_spans()] == ["#1 add basket"]


def test_an_unnamed_agent_stays_on_the_shared_resource() -> None:
    capture = ResourceCapture()

    capture.observer.agent_run(named_record(None))

    assert [span.name for span in capture.shared.get_finished_spans()] == ["execute_tool bash", "invoke_agent copilot"]
    assert "planner" not in capture.exporters


def test_the_agent_name_is_an_attribute_of_its_span() -> None:
    capture = ResourceCapture()

    capture.observer.agent_run(named_record("planner"))

    span = capture.exporters["planner"].get_finished_spans()[-1]
    assert span.attributes["gen_ai.agent.name"] == "planner"


def test_the_public_factory_builds_a_resource_per_agent_observer() -> None:
    import loop

    observer = loop.otlp_observer(endpoint="http://localhost:1", service_name="t", resource_per_agent=True)

    assert observer._resource_providers is not None


def test_a_workflow_span_with_a_role_exports_as_that_resource_and_still_nests() -> None:
    capture = ResourceCapture()

    with capture.observer.span("spec", None):
        with capture.observer.span("request", None, role="engineer"):
            pass

    request = capture.exporters["engineer"].get_finished_spans()[0]
    spec = capture.shared.get_finished_spans()[0]
    assert request.parent.span_id == spec.context.span_id


def test_the_shared_role_names_match_the_adw_vocabulary() -> None:
    from loop import Role

    assert Role.ALL == ("engineer", "code", "planner", "developer", "reviewer")



def test_retained_worktree_closes_span_without_claiming_removal() -> None:
    """Given retained edits, when a run fails, then its span ends with a retention fact."""
    capture = Capture()
    capture.observer.worktree(WorktreeEvent("ready", CWD, START, None))
    capture.observer.worktree(WorktreeEvent("retained", CWD, START + timedelta(seconds=7), False))
    worktree = capture.span("loop.worktree")
    assert worktree.attributes["loop.worktree.retained"] is True
    assert worktree.status.status_code == trace.StatusCode.ERROR
