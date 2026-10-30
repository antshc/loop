"""The factory's OpenTelemetry spans against an in-memory exporter, with a fake runner Agent Run, the tee and a failing exporter."""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from loop import (  # noqa: E402
    AgentRequest, AgentResult, AgentWorkspace, AgentContext, FakeCliRunner, FakeReply, FakeToolUse, RunContext, Usage,
    fake_agent,
)
from loop.observe.otel import OtlpObserver  # noqa: E402

from software_factory.runtime import (  # noqa: E402
    Fail, Flow, HarnessCommits, LogSpans, NullSpans, Pass, Stage, Workflow, WorkspaceOpener, handoff, rework,
)
from software_factory.shared.telemetry import OtelSpans, Tee  # noqa: E402
from software_factory.tests.runtime.harness import TESTER, Ask, Rig, WorkCtx, editing  # noqa: E402
from software_factory.tests.runtime.support import NoInput, Routed  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


class Capture:
    """An in-memory exporter behind one tracer provider per resource, as a resource-per-agent observer exports."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self._providers: dict[str, TracerProvider] = {}
        self.provider = self.provider_for("loop")

    def provider_for(self, resource: str) -> TracerProvider:
        """The provider exporting as `resource`."""
        if resource not in self._providers:
            provider = TracerProvider(resource=Resource.create({"service.name": resource}))
            provider.add_span_processor(SimpleSpanProcessor(self.exporter))
            self._providers[resource] = provider
        return self._providers[resource]

    def spans(self) -> list[ReadableSpan]:
        """Every finished span, in the order it ended."""
        return list(self.exporter.get_finished_spans())

    def named(self, name: str) -> ReadableSpan:
        """The one finished span called `name`."""
        found = [span for span in self.spans() if span.name == name]
        assert len(found) == 1, f"{name}: {len(found)} spans in {[span.name for span in self.spans()]}"
        return found[0]

    def parent_name(self, span: ReadableSpan) -> str | None:
        """The name of a span's parent, or None for a root."""
        if span.parent is None:
            return None
        return next(other.name for other in self.spans() if other.context.span_id == span.parent.span_id)


class ByRole:
    """A tracing observer whose spans export as the role's own resource, like `resource_per_agent`."""

    def __init__(self, capture: Capture) -> None:
        self._capture = capture

    def agent_run(self, record: Any) -> None:
        return None

    def worktree(self, event: Any) -> None:
        return None

    def span(self, name: str, attributes: Any = None, *, role: str | None = None) -> Any:
        return self._capture.provider_for(role or "loop").get_tracer("test").start_as_current_span(name, attributes=attributes)


class Broken:
    """A tracing observer, and the span it yields, whose every call raises."""

    def __init__(self, *, open_fails: bool = False) -> None:
        self._open_fails = open_fails

    def agent_run(self, record: Any) -> None:
        return None

    def worktree(self, event: Any) -> None:
        return None

    @contextmanager
    def span(self, name: str, attributes: Any = None, *, role: str | None = None) -> Any:
        if self._open_fails:
            raise RuntimeError("collector down")
        yield self
        raise RuntimeError("exporter down")

    def set_attribute(self, key: str, value: Any) -> None:
        raise RuntimeError("exporter down")

    def add_event(self, name: str, attributes: Any = None) -> None:
        raise RuntimeError("exporter down")

    def set_status(self, status: Any) -> None:
        raise RuntimeError("exporter down")


class Observed:
    """A fake CLI Agent Run in its own Worktree, observed, run the way a Stage's agent runs it."""

    def __init__(self, observer: OtlpObserver) -> None:
        reply = FakeReply("done", Usage(input_tokens=10, output_tokens=5), (FakeToolUse("bash", seconds=0),), model="fake-1")
        self._runner = FakeCliRunner(lambda request, context: reply, speed=0)
        self._observer = observer

    def run(self, request: AgentRequest) -> AgentResult:
        with fake_agent(self._runner).with_git().with_observer(self._observer, readers=[self._runner]).open() as worktree:
            return worktree.agent().run(request)


def observed_workspace(path: Path, observer: OtlpObserver) -> WorkspaceOpener[Any]:
    """A workspace whose agent runs are observed, so Loop's own spans start inside the Stage attempt."""
    workspace = AgentWorkspace(path, lambda *_: Observed(observer), RunContext(AgentContext()), None)
    return WorkspaceOpener("checkout", NoInput, lambda _: nullcontext(workspace))


def agent_stage(gate: Any = None) -> Stage[Any, Any, Any]:
    """An agent Stage that passes unless given another gate."""
    stage = Stage("work", NoInput, Routed).agent(TESTER, lambda i, path: Ask("go"))
    return stage.gate(gate or (lambda i, reply, evidence: Pass(Routed("done"))))


def effect_stage(gate: Any = None) -> Stage[Any, Any, Any]:
    """An effect Stage that passes unless given another gate."""
    stage = Stage("notify", NoInput, Routed).effect(lambda i: None, reconcile=lambda i: None)
    return stage.gate(gate or (lambda i, reply, evidence: Pass(Routed("done"))))


def test_agent_run_tool_and_worktree_spans_nest_under_the_stage_attempt(tmp_path: Path) -> None:
    """Given an agent Stage run with OtelSpans, when the trace is exported, then run, scope and attempt nest and Loop's spans sit under the attempt."""
    capture = Capture()
    observer = OtlpObserver(tracer_provider=capture.provider, meter_provider=MeterProvider())
    rig = Rig(tmp_path)
    rig.spans = OtelSpans(observer)
    workflow = Workflow("t").context(WorkCtx).within(observed_workspace(rig.repo.path, observer), Flow("repo").stage(agent_stage()))

    result = rig.run(workflow)

    assert result.status == "completed"
    assert capture.parent_name(capture.named("t")) is None
    assert capture.parent_name(capture.named("checkout")) == "t"
    assert capture.parent_name(capture.named("work")) == "checkout"
    assert capture.parent_name(capture.named("invoke_agent copilot")) == "work"
    assert capture.parent_name(capture.named("loop.worktree")) == "work"
    assert capture.parent_name(capture.named("execute_tool bash")) == "invoke_agent copilot"
    assert len({span.context.trace_id for span in capture.spans()}) == 1


def test_an_agent_attempt_exports_under_the_agent_role_and_an_effect_attempt_under_code(tmp_path: Path) -> None:
    """Given an agent Stage then an effect Stage, when exported, then the run is engineer, the agent attempt tester, the scope and effect code."""
    capture = Capture()
    rig = Rig(tmp_path)
    rig.spans = OtelSpans(ByRole(capture))
    body = Flow("repo").stage(agent_stage()).stage(effect_stage())

    rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), body))

    resources = {span.name: span.resource.attributes["service.name"] for span in capture.spans()}
    assert resources == {"t": "engineer", "checkout": "code", "work": "tester", "notify": "code"}


def test_a_failed_attempt_scope_and_run_have_error_status_and_the_failure_attributes(tmp_path: Path) -> None:
    """Given an attempt that fails, when exported, then attempt, scope and run are ERROR and carry the outcome and reason."""
    capture = Capture()
    rig = Rig(tmp_path)
    rig.spans = OtelSpans(ByRole(capture))
    body = Flow("repo").stage(agent_stage(lambda i, reply, evidence: Fail("tests red")))

    result = rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), body))

    assert result.status == "failed"
    attempt, scope, run = (capture.named(name) for name in ("work", "checkout", "t"))
    assert [span.status.status_code for span in (attempt, scope, run)] == [StatusCode.ERROR] * 3
    assert [span.status.description for span in (attempt, scope, run)] == ["tests red"] * 3
    assert attempt.attributes["factory.stage.outcome"] == "fail"
    assert attempt.attributes["factory.stage.reason"] == "tests red"
    assert scope.attributes["factory.scope.status"] == "failed"
    assert (run.attributes["factory.run.status"], run.attributes["factory.run.reason"]) == ("failed", "tests red")


def test_a_passing_run_has_no_error_status(tmp_path: Path) -> None:
    """Given a run that passes, when exported, then no span is an error."""
    capture = Capture()
    rig = Rig(tmp_path)
    rig.spans = OtelSpans(ByRole(capture))

    rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("repo").stage(agent_stage())))

    assert {span.status.status_code for span in capture.spans()} == {StatusCode.UNSET}


def test_gate_contract_commit_restore_rework_and_hand_off_are_events_on_the_attempt_span(tmp_path: Path) -> None:
    """Given a failed attempt that reworks and hands off, when exported, then the attempt spans carry their factory events."""
    capture = Capture()
    rig = Rig(tmp_path).agent_does(editing())
    rig.spans = OtelSpans(ByRole(capture))
    verdicts = [Fail("tests red"), Pass(Routed("done"))]
    worker = agent_stage(lambda i, reply, evidence: verdicts.pop(0)).contract(HarnessCommits(message=lambda i, event: "implemented"))
    failing = Stage("fix", NoInput, Routed).agent(TESTER, lambda i, path: Ask("go")).contract(HarnessCommits(message=lambda i, event: "fixed"))
    failing = failing.gate(lambda i, reply, evidence: Fail("no"))
    body = Flow("repo").stage(worker.on_fail(rework(max_attempts=2))).stage(failing.on_fail(handoff(effect_stage())))

    rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), body))

    work = [span for span in capture.spans() if span.name == "work"]
    fix = capture.named("fix")
    assert [[event.name for event in span.events] for span in work] == [
        ["factory.gate", "factory.restore", "factory.rework"],
        ["factory.gate", "factory.contract", "factory.commit"],
    ]
    assert dict(work[0].events[2].attributes) == {"next_attempt": 2, "budget": 2}
    assert dict(work[0].events[0].attributes) == {"verdict": "fail", "reason": "tests red"}
    assert [event.name for event in fix.events] == ["factory.gate", "factory.restore", "factory.handoff"]


def test_the_tee_exports_and_logs_each_finished_span(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Given the tee of OtelSpans and LogSpans, when a run finishes, then every exported span is also one console line."""
    capture = Capture()
    rig = Rig(tmp_path)
    rig.spans = Tee(OtelSpans(ByRole(capture)), LogSpans())
    caplog.set_level(logging.INFO, logger="software_factory.runtime")
    body = Flow("repo").stage(agent_stage(lambda i, reply, evidence: Fail("no")))

    rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), body))

    lines = [record.getMessage() for record in caplog.records]
    assert len(capture.spans()) == len(lines) == 3
    assert lines == ["checkout · work attempt 1 · fail · no", "checkout · failed · no", "t · failed · no"]
    assert {span.status.status_code for span in capture.spans()} == {StatusCode.ERROR}


@pytest.mark.parametrize("observer", [Broken(), Broken(open_fails=True)], ids=["exporter fails", "observer fails to open"])
def test_a_failing_exporter_or_observer_leaves_the_run_result_unchanged(tmp_path: Path, observer: Broken) -> None:
    """Given an observer or exporter that raises on every call, when a run fails and one passes, then each result matches the unobserved run."""
    for gate, name in ((lambda i, reply, evidence: Fail("no"), "failing"), (None, "passing")):
        body = Flow("repo").stage(agent_stage(gate))
        plain, watched = Rig(tmp_path / name / "plain"), Rig(tmp_path / name / "watched")
        plain.spans, watched.spans = NullSpans(), OtelSpans(observer)

        expected = plain.run(Workflow("t").context(WorkCtx).within(plain.checkout(), body))
        actual = watched.run(Workflow("t").context(WorkCtx).within(watched.checkout(), body))

        assert (actual.status, actual.reason, actual.exit_code) == (expected.status, expected.reason, expected.exit_code)


def test_the_tee_keeps_logging_when_the_exporter_fails(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Given a tee whose OpenTelemetry side raises, when a run finishes, then the console lines are still logged."""
    rig = Rig(tmp_path)
    rig.spans = Tee(OtelSpans(Broken()), LogSpans())
    caplog.set_level(logging.INFO, logger="software_factory.runtime")

    result = rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("repo").stage(agent_stage())))

    assert result.status == "completed"
    assert [record.getMessage() for record in caplog.records if record.name == "software_factory.runtime"][-1] == "t · completed"


def test_without_the_extra_the_factory_runs_with_console_logging_and_reports_the_extra_only_when_otel_is_created(tmp_path: Path) -> None:
    """Given no opentelemetry package, when the factory is imported and run with LogSpans, then it works and only creating the OTLP observer reports the extra."""
    script = textwrap.dedent(
        """
        import sys
        sys.modules["opentelemetry"] = None
        from pathlib import Path
        from software_factory.runtime import Ledger, LogSpans, Pass, Services, Stage, Workflow, run
        from software_factory.shared.telemetry import OtlpObserverFactory, Tee
        from software_factory.tests.runtime.harness import WorkCtx
        from software_factory.tests.runtime.support import Alpha, NoInput

        stage = Stage("work", NoInput, Alpha).action(lambda i: None).gate(lambda i, r, e: Pass(Alpha()))
        result = run(Workflow("t").context(WorkCtx).stage(stage), WorkCtx(), services=Services(), ledger=Ledger(Path(sys.argv[1])), spans=Tee(LogSpans()))
        assert result.status == "completed", result
        try:
            OtlpObserverFactory("factory")("http://localhost:4318")
        except ImportError as exception:
            assert "loop[otel]" in str(exception), exception
        else:
            raise SystemExit("creating the OTLP observer should need the extra")
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], cwd=REPO_ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": f"{REPO_ROOT / 'loop' / 'src'}{':'}{REPO_ROOT}", "PATH": ""},
    )

    assert completed.returncode == 0, completed.stderr
