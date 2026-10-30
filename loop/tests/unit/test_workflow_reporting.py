from __future__ import annotations

import builtins
import io
import logging
from contextlib import contextmanager
from pathlib import Path

import pytest

from loop import Kind, Phases, Role, WorkflowBuilder


def builder() -> WorkflowBuilder:
    return (
        WorkflowBuilder("Basket")
        .phase("ticket", "Add product", Kind.AGENT, Role.DEVELOPER)
        .phase("commit", "Commit changes", Kind.CODE, Role.CODE, visible=False)
        .phase("verify", "Verify basket", Kind.CODE, Role.CODE)
    )


def test_console_reports_only_transitions_and_never_payloads_or_gates(caplog) -> None:
    """Given detailed facts, when reporting to console, then only high-level transitions appear once."""
    with caplog.at_level(logging.INFO, logger="loop.workflow"):
        with builder().console().build().run(spec=42) as run:
            with run.phase("ticket", ticket=43, attempt=2) as phase:
                phase.set(prompt="PRIVATE_PROMPT", output="PRIVATE_OUTPUT")
                phase.gate("PRIVATE_GATE", True)
            with run.phase("commit"):
                pass
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 4
    assert sum("Add product" in message and "completed" in message for message in messages) == 1
    assert "ticket #43 · attempt 2" in messages[1]
    assert not any("PRIVATE" in message or "Commit changes" in message for message in messages)
    assert not any(record.exc_info for record in caplog.records)


def test_failed_console_reason_is_short_and_single_line(caplog) -> None:
    """Given a long failure, when reported, then it is a bounded high-level line."""
    with caplog.at_level(logging.INFO, logger="loop.workflow"):
        with builder().console().build().run() as run:
            with run.phase("ticket") as phase:
                phase.fail("failure\n" + "x" * 1000)
    warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert "\n" not in warning.getMessage()
    assert len(warning.getMessage()) < 260


def test_core_import_and_direct_phases_do_not_load_optional_packages(monkeypatch) -> None:
    """Given unavailable extras, when using core tracking, then no optional import is attempted."""
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith(("rich", "opentelemetry")):
            raise AssertionError(f"Unexpected optional import: {name}")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    from loop import WorkflowBuilder as ImportedBuilder
    with Phases().phase("direct", Kind.CODE, Role.CODE):
        pass
    with ImportedBuilder("bare").build().run() as run:
        assert run.outcome.status == "running"


def test_missing_visualization_extra_has_an_actionable_error(monkeypatch) -> None:
    """Given no Rich, when visualization is requested, then setup explains the extra."""
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "rich":
            raise ModuleNotFoundError("rich")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(ImportError, match=r"loop\[visualization\]"):
        builder().visualize().build()


def test_redirected_visualization_shows_final_states_and_no_control_codes(caplog) -> None:
    """Given redirected output, when a run ends, then one final table includes retries and skips."""
    pytest.importorskip("rich")
    output = io.StringIO()
    with caplog.at_level(logging.INFO, logger="loop.workflow"):
        with builder().console().visualize(stream=output).build().run() as run:
            with run.phase("ticket", ticket=43, attempt=1) as phase:
                phase.fail("try again")
            with run.phase("ticket", ticket=43, attempt=2):
                pass
            with run.phase("commit"):
                pass
            run.skip("verify", "external verifier")
    rendered = output.getvalue()
    assert "failed" in rendered and "completed" in rendered and "skipped" in rendered
    assert "Commit changes" not in rendered
    assert "\x1b" not in rendered
    assert sum("Add product" in record.getMessage() and "completed" in record.getMessage()
               for record in caplog.records) == 1


def test_live_visualization_closes_on_exception(monkeypatch) -> None:
    """Given a terminal view, when the body raises, then Rich restores the terminal."""
    pytest.importorskip("rich")
    from rich.console import Console
    from rich.live import Live
    stopped = []
    original_stop = Live.stop
    def stop(live):
        stopped.append(live)
        original_stop(live)
    monkeypatch.setattr(Console, "is_terminal", property(lambda self: True))
    monkeypatch.setattr(Live, "stop", stop)
    with pytest.raises(ValueError, match="broken"):
        with builder().visualize(stream=io.StringIO()).build().run() as run:
            with run.phase("ticket"):
                raise ValueError("broken")
    assert stopped
    assert not stopped[0].is_started


def test_reporter_failures_do_not_replace_the_body_exception() -> None:
    """Given broken output, when the body raises, then its original exception survives."""
    class Broken:
        def emit(self, point, phase):
            raise OSError("closed stream")
    error = ValueError("basket invalid")
    with pytest.raises(ValueError) as raised:
        with builder().build().run() as run:
            run._reporters = (Broken(),)
            with run.phase("ticket"):
                raise error
    assert raised.value is error
    assert run.outcome.status == "failed"


def test_telemetry_open_and_close_failures_do_not_change_execution() -> None:
    """Given an unavailable observer, when running phases, then business outcomes remain intact."""
    class BrokenObserver:
        def span(self, *args, **kwargs):
            raise OSError("exporter unavailable")
    with builder().otel(BrokenObserver()).build().run() as run:
        with run.phase("ticket"):
            pass
    assert run.outcome.status == "completed"

    class BrokenClose:
        @contextmanager
        def span(self, *args, **kwargs):
            try:
                yield object()
            finally:
                raise OSError("cleanup failed")
    with pytest.raises(ValueError, match="original"):
        with builder().otel(BrokenClose()).build().run() as run:
            raise ValueError("original")


def test_otel_phase_gates_and_agent_runs_share_parentage_inside_a_worktree() -> None:
    """Given a worktree and phases, when an agent runs, then its current phase parents it."""
    pytest.importorskip("opentelemetry.sdk")
    from datetime import datetime, timezone
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from loop import AgentRunRecord, WorktreeEvent
    from loop.observe.otel import OtlpObserver
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observer = OtlpObserver(tracer_provider=provider)
    now, cwd = datetime.now(timezone.utc), Path("/fake/basket")
    with builder().otel(observer).build().run(spec=42) as run:
        observer.worktree(WorktreeEvent("ready", cwd, now, None))
        with run.phase("ticket", ticket=43) as phase:
            observer.agent_run(AgentRunRecord(
                "copilot", "basket", None, cwd, now, now, 0, None,
                None, None, None, (), "private prompt", "private output",
            ))
            phase.gate("basket changed", False, "no changes")
        with run.phase("commit"):
            pass
        observer.worktree(WorktreeEvent("removed", cwd, now, True))
        run.set(ready=False)
        run.fail("PR remains draft")
    spans = {span.name: span for span in exporter.get_finished_spans()}
    ticket = spans["Add product"]
    assert ticket.parent.span_id == spans["Basket"].context.span_id
    assert spans["invoke_agent copilot"].parent.span_id == ticket.context.span_id
    assert ticket.status.status_code == trace.StatusCode.ERROR
    assert ticket.events[0].attributes["loop.gate.ok"] is False
    assert spans["Basket"].attributes["loop.ready"] is False
    assert spans["Commit changes"].attributes["loop.phase.status"] == "completed"
    assert "gen_ai.input.messages" not in spans["invoke_agent copilot"].attributes
    assert not trace.get_current_span().get_span_context().is_valid
    provider.shutdown()


def test_fake_example_covers_recovery_and_failed_verification(capsys) -> None:
    """Given the documented example, when invoked, then success and failure return correct exit codes."""
    from workflows.workflow_reporting import main
    assert main([]) == 0
    success = capsys.readouterr().err
    assert "attempt 1 failed" in success
    assert "eShop development · spec #42 completed" in success
    assert main(["--fail-verification"]) == 1
    failure = capsys.readouterr().err
    assert "PR remains a draft" in failure
    assert "Review pull request · spec #42 skipped" in failure


def test_broken_host_logging_cannot_fail_or_replace_a_workflow_error() -> None:
    """Given a broken host handler, when progress is emitted, then execution keeps its own outcome."""
    class BrokenHandler(logging.Handler):
        def emit(self, record):
            raise OSError("log destination closed")
    logger = logging.getLogger("loop.workflow")
    handler = BrokenHandler()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with builder().console().build().run() as recovered:
            with recovered.phase("ticket"):
                pass
        assert recovered.outcome.status == "completed"
        with pytest.raises(ValueError, match="original"):
            with builder().console().build().run() as run:
                with run.phase("ticket"):
                    raise ValueError("original")
        assert run.outcome.status == "failed"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_documented_python_examples_are_runnable() -> None:
    """Given the usage guide, when its builder examples execute, then they use the public interface."""
    import re
    guide = Path(__file__).parents[3] / "USAGE.md"
    section = guide.read_text().split("## Optional Workflow Builder\n", 1)[1].split("## Logging\n", 1)[0]
    snippets = re.findall(r"```python\n(.*?)```", section, re.DOTALL)
    for index in (0, 1, 3):
        exec(compile(snippets[index], f"USAGE.md example {index}", "exec"), {})
