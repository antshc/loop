"""Observation seam tests: session-log reading, the observing decorators, the builder wiring and the OTel adapter."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from loop import (
    AgentBuilder,
    AgentContext,
    AgentProfile,
    AgentRequest,
    AgentRunRecord,
    CliOutcome,
    CopilotLogReader,
    DockerRuntime,
    GitOptions,
    NativeHandle,
    Resume,
    SessionName,
    Start,
    WorktreeEvent,
    copilot,
)
from loop.clis import ObservingCliRunner
from loop.clis.copilot_log import copilot_session_id
from loop.git import ObservingGitService
from loop.run import RunContext

ROOT = Path(__file__).parents[3]
SRC_ROOT = ROOT / "loop" / "src"
SRC = SRC_ROOT / "loop"
NAME = SessionName("ticket-7")
PROFILE = AgentProfile(copilot, model="claude-haiku-4.5")


def event(kind: str, second: str, **data: object) -> dict[str, object]:
    return {"type": kind, "data": data, "timestamp": f"2026-10-23T13:00:{second}Z"}


def usage_record(initiator: str, inputs: int, cache_read: int, cache_write: int, output: int) -> dict[str, object]:
    details = [
        {"tokenType": "input", "tokenCount": inputs - cache_read - cache_write, "costPerBatch": 200_000_000_000, "batchSize": 1_000_000},
        {"tokenType": "output", "tokenCount": output, "costPerBatch": 1_000_000_000_000, "batchSize": 1_000_000},
    ]
    return {
        "usage": {
            "model": "claude-haiku-4.5", "inputTokens": inputs, "outputTokens": output,
            "cacheReadTokens": cache_read, "cacheWriteTokens": cache_write, "reasoningTokens": 5,
            "cost": 1.0, "initiator": initiator, "reasoningEffort": "medium",
            "copilotUsage": {"tokenDetails": details, "totalNanoAiu": 1_000_000_000},
        }
    }


def first_turn(session_id: str) -> list[dict[str, object]]:
    return [
        event("session.start", "00.000", sessionId=session_id, selectedModel="claude-haiku-4.5"),
        event("user.message", "00.200", content="do the thing"),
        event("tool.execution_start", "01.000", toolCallId="t1", toolName="bash", arguments={"command": "git status"}),
        event("tool.execution_complete", "01.500", toolCallId="t1", success=True, result={"content": "clean"}),
        event("session.usage_record", "02.000", **usage_record("user", 1_000, 600, 300, 40)),
        event("session.usage_record", "03.000", **usage_record("agent", 2_000, 1_500, 100, 60)),
        event("assistant.message", "03.100", content="done", model="claude-haiku-4.5"),
        event("session.shutdown", "03.200", totalPremiumRequests=1),
    ]


def second_turn() -> list[dict[str, object]]:
    return [
        event("session.resume", "10.000", selectedModel="claude-haiku-4.5"),
        event("user.message", "10.200", content="again"),
        event("tool.execution_start", "11.000", toolCallId="t2", toolName="edit", arguments={}),
        event("tool.execution_complete", "11.250", toolCallId="t2", success=False, result={"content": "boom"}),
        event("session.usage_record", "12.000", **usage_record("user", 500, 0, 0, 10)),
        event("assistant.message", "12.100", content="again done", model="claude-haiku-4.5"),
        event("session.shutdown", "12.200", totalPremiumRequests=2),
    ]


def write_log(home: Path, name: SessionName, events: list[dict[str, object]]) -> None:
    path = home / "session-state" / copilot_session_id(name) / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n")


class RecordingObserver:
    def __init__(self) -> None:
        self.records: list[AgentRunRecord] = []
        self.worktrees: list[WorktreeEvent] = []

    def agent_run(self, record: AgentRunRecord) -> None:
        self.records.append(record)

    def worktree(self, event: WorktreeEvent) -> None:
        self.worktrees.append(event)


class BrokenObserver:
    def agent_run(self, record: AgentRunRecord) -> None:
        raise RuntimeError("sink down")

    def worktree(self, event: WorktreeEvent) -> None:
        raise RuntimeError("sink down")


class FakeRunner:
    def __init__(self, outcome: CliOutcome | None = None, error: Exception | None = None) -> None:
        self.profiles: list[AgentProfile] = []
        self._outcome = outcome or CliOutcome("ok", NativeHandle("copilot", NAME.value), 0)
        self._error = error

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Start | Resume, context: RunContext) -> CliOutcome:
        self.profiles.append(profile)
        if self._error is not None:
            raise self._error
        return self._outcome


def start_turn() -> Start:
    return Start(NAME)


def resume_turn() -> Resume:
    return Resume(NAME, NativeHandle("copilot", NAME.value))


def observed(home: Path, runner: FakeRunner | None = None, observer: object | None = None) -> tuple[ObservingCliRunner, RecordingObserver]:
    recording = RecordingObserver()
    decorated = ObservingCliRunner(runner or FakeRunner(), observer or recording, [CopilotLogReader(home)])
    return decorated, recording


def test_reader_sums_the_turn_and_splits_cached_input(tmp_path: Path) -> None:
    write_log(tmp_path, NAME, first_turn("s"))

    reading = CopilotLogReader(tmp_path).read(start_turn())

    assert reading is not None
    usage = reading.usage
    assert (usage.api_calls, usage.input_tokens, usage.output_tokens) == (2, 500, 100)
    assert (usage.cache_read_tokens, usage.cache_write_tokens, usage.total_input_tokens) == (2_100, 400, 3_000)
    assert usage.premium_requests == 1.0
    assert usage.aiu == pytest.approx({"input": 0.1, "output": 0.1})
    assert reading.model == "claude-haiku-4.5" and reading.reasoning_effort == "medium"
    [tool] = reading.tools
    assert (tool.name, tool.ok, (tool.ended_at - tool.started_at).total_seconds()) == ("bash", True, 0.5)


def test_reader_slices_a_resumed_turn_from_its_resume_event(tmp_path: Path) -> None:
    write_log(tmp_path, NAME, first_turn("s") + second_turn())

    reading = CopilotLogReader(tmp_path).read(resume_turn())

    assert reading is not None
    assert (reading.usage.api_calls, reading.usage.output_tokens, reading.usage.premium_requests) == (1, 10, 1.0)
    assert [(tool.name, tool.ok) for tool in reading.tools] == [("edit", False)]


def test_reader_reports_nothing_for_missing_log_or_a_log_without_per_call_usage(tmp_path: Path) -> None:
    reader = CopilotLogReader(tmp_path)
    assert reader.read(start_turn()) is None

    write_log(tmp_path, NAME, [event("session.start", "00.000", sessionId="s"), event("session.shutdown", "01.000")])
    assert reader.read(start_turn()) is None


def test_reader_pins_only_a_start() -> None:
    reader = CopilotLogReader()
    assert reader.start_args(start_turn()) == ("--session-id", copilot_session_id(NAME))
    assert reader.start_args(resume_turn()) == ()


def test_runner_pins_start_reports_usage_and_returns_the_inner_outcome(tmp_path: Path) -> None:
    write_log(tmp_path, NAME, first_turn("s"))
    inner = FakeRunner()
    runner, observer = observed(tmp_path, inner)

    outcome = runner.run(PROFILE, AgentRequest("go"), start_turn(), RunContext(AgentContext(cwd=tmp_path)))

    assert outcome.output == "ok"
    assert inner.profiles[0].args[-2:] == ("--session-id", copilot_session_id(NAME))
    [record] = observer.records
    assert (record.cli, record.session, record.exit_code, record.error) == ("copilot", NAME.value, 0, None)
    assert record.usage is not None and record.usage.api_calls == 2
    assert record.native_id == copilot_session_id(NAME) and record.prompt == "go" and record.output == "ok"
    assert record.started_at <= record.ended_at


def test_runner_leaves_the_profile_alone_on_resume(tmp_path: Path) -> None:
    inner = FakeRunner()
    runner, _ = observed(tmp_path, inner)

    runner.run(PROFILE, AgentRequest("go"), resume_turn(), RunContext())

    assert inner.profiles == [PROFILE]


def test_runner_reports_unobservable_usage_as_none_not_zero(tmp_path: Path) -> None:
    runner, observer = observed(tmp_path)

    runner.run(PROFILE, AgentRequest("go"), start_turn(), RunContext())

    [record] = observer.records
    assert record.usage is None and record.tools == () and record.native_id is None
    assert record.model == "claude-haiku-4.5"


def test_runner_without_a_reader_for_the_cli_adds_no_argument() -> None:
    inner = FakeRunner()
    recording = RecordingObserver()

    ObservingCliRunner(inner, recording).run(PROFILE, AgentRequest("go"), start_turn(), RunContext())

    assert inner.profiles == [PROFILE] and recording.records[0].usage is None


def test_runner_reports_a_raise_then_reraises(tmp_path: Path) -> None:
    failure = subprocess.CalledProcessError(2, ["copilot"])
    runner, observer = observed(tmp_path, FakeRunner(error=failure))

    with pytest.raises(subprocess.CalledProcessError):
        runner.run(PROFILE, AgentRequest("go"), start_turn(), RunContext())

    [record] = observer.records
    assert record.exit_code is None and "CalledProcessError" in (record.error or "") and record.output is None


def test_a_failing_observer_never_fails_the_run(tmp_path: Path) -> None:
    runner, _ = observed(tmp_path, observer=BrokenObserver())

    assert runner.run(PROFILE, AgentRequest("go"), start_turn(), RunContext()).exit_code == 0


def test_a_garbled_log_never_fails_the_run(tmp_path: Path) -> None:
    path = tmp_path / "session-state" / copilot_session_id(NAME) / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{not json\n")
    runner, observer = observed(tmp_path)

    assert runner.run(PROFILE, AgentRequest("go"), start_turn(), RunContext()).exit_code == 0
    assert observer.records[0].usage is None


class FakeGit:
    def __init__(self, path: Path, remove_error: Exception | None = None) -> None:
        self._path = path
        self._remove_error = remove_error

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        yield self._path
        if self._remove_error is not None:
            raise self._remove_error


def points(observer: RecordingObserver) -> list[tuple[str, bool | None]]:
    return [(item.point, item.ok) for item in observer.worktrees]


def test_worktree_events_wrap_a_successful_run(tmp_path: Path) -> None:
    observer = RecordingObserver()

    with ObservingGitService(FakeGit(tmp_path), observer).open(tmp_path, GitOptions()) as path:
        assert path == tmp_path

    assert points(observer) == [("ready", None), ("removing", True), ("retained", True)]


def test_worktree_events_report_a_failed_body_and_propagate_it(tmp_path: Path) -> None:
    observer = RecordingObserver()

    with pytest.raises(ValueError):
        with ObservingGitService(FakeGit(tmp_path), observer).open(tmp_path, GitOptions()):
            raise ValueError("agent failed")

    assert points(observer) == [("ready", None), ("removing", False), ("retained", False)]


def test_worktree_removal_failure_is_not_reported_as_ok(tmp_path: Path) -> None:
    observer = RecordingObserver()

    with pytest.raises(OSError):
        with ObservingGitService(FakeGit(tmp_path, OSError("merge")), observer).open(tmp_path, GitOptions()):
            pass

    assert points(observer) == [("ready", None), ("removing", True), ("retained", False)]


def test_a_failing_observer_never_breaks_the_worktree(tmp_path: Path) -> None:
    with ObservingGitService(FakeGit(tmp_path), BrokenObserver()).open(tmp_path, GitOptions()) as path:
        assert path == tmp_path


def test_builder_reports_the_worktree_and_the_agent_run(tmp_path: Path) -> None:
    observer = RecordingObserver()
    inner = FakeRunner()
    builder = AgentBuilder(inner, FakeGit(tmp_path), DockerRuntime(), AgentContext(cwd=tmp_path))

    with builder.with_git().with_observer(observer, readers=[CopilotLogReader(tmp_path)]).open() as workspace:
        workspace.agent().run(AgentRequest("go"))

    assert [item.point for item in observer.worktrees] == ["ready", "removing", "retained"]
    [record] = observer.records
    assert record.cwd == tmp_path and "--session-id" in inner.profiles[0].args


def test_builder_rejects_a_second_observer() -> None:
    builder = AgentBuilder(FakeRunner(), FakeGit(Path(".")), DockerRuntime()).with_observer(RecordingObserver())

    with pytest.raises(ValueError):
        builder.with_observer(RecordingObserver())


def test_builder_without_an_observer_adds_no_argument(tmp_path: Path) -> None:
    inner = FakeRunner()

    AgentBuilder(inner, FakeGit(tmp_path), DockerRuntime(), AgentContext(cwd=tmp_path)).create().run(AgentRequest("go"))

    assert inner.profiles[0].args == ()


def test_only_the_otel_adapter_imports_opentelemetry() -> None:
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path.name != "otel.py" and "opentelemetry" in path.read_text()
    ]

    assert offenders == []


def test_loop_imports_without_opentelemetry_and_reports_the_missing_extra() -> None:
    script = (
        "import sys; sys.modules['opentelemetry'] = None\n"
        "import loop\n"
        "try:\n"
        "    loop.otlp_observer()\n"
        "except ImportError as error:\n"
        "    print('loop[otel]' in str(error))\n"
    )

    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env={"PYTHONPATH": str(SRC_ROOT)}, capture_output=True, text=True)

    assert result.stdout.strip() == "True", result.stderr

