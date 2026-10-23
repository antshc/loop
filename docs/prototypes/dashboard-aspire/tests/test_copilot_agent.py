from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.trace import StatusCode

from conftest import Capture, per_call_log, write_log
from fakeloop.copilot_agent import (
    AgentProcessError, CopilotAgent, CopilotOptions, ProcessResult, _envelope)
from fakeloop.model import AgentConfig, Attempt, GateCheck, PhaseParams
from fakeloop.runner import Run
from loop import Prompt

CONFIG = AgentConfig("developer", "claude-haiku-4.5", "#5ad2dd")
PASSING = Attempt((), {"status": "scripted"}, (GateCheck("build", True),))


class FakeCli:
    """Stands in for `copilot`: records argv and leaves a session log like the real CLI does."""

    def __init__(self, home: Path, exit_code: int = 0, reply: str = 'ok {"status": "completed"}') -> None:
        self.home, self.exit_code, self.reply = home, exit_code, reply
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: list[str], cwd: Path, timeout: float) -> ProcessResult:
        self.calls.append((argv, cwd))
        if self.exit_code == 0:
            session_id = argv[argv.index("--session-id") + 1]
            write_log(self.home / "session-state" / session_id / "events.jsonl",
                      per_call_log(session_id, self.reply))
        return ProcessResult(self.exit_code, "", "boom" if self.exit_code else "")


def agent_run(capture: Capture, cli: FakeCli, tmp_path: Path) -> tuple[Run, CopilotAgent]:
    options = CopilotOptions(repo=tmp_path, home=cli.home, run=cli)
    agent = CopilotAgent(CONFIG, "You implement tickets.", options)
    return Run(capture.telemetry, "e", "dev", speed=0, agents=lambda *_: agent), agent


def call(run: Run, agent: CopilotAgent) -> dict[str, object]:
    params = PhaseParams("develop_1", "agent", "developer", "Implement.")
    with run.phase(params) as ph:
        return agent.call(ph, Prompt("Do {{X}}", {"X": "it"}), "BuildOutput", "build", [PASSING])


def test_agent_span_carries_real_usage_model_and_tools(capture: Capture, tmp_path: Path) -> None:
    cli = FakeCli(tmp_path / "home")
    run, agent = agent_run(capture, cli, tmp_path)

    envelope = call(run, agent)
    run.finish()

    span = capture.span("invoke_agent developer")
    attributes = span.attributes
    assert envelope == {"status": "completed"}
    assert attributes["gen_ai.conversation.id"] == agent.session_id
    assert attributes["gen_ai.response.model"] == "claude-haiku-4.5"
    assert attributes["gen_ai.usage.input_tokens"] == 3_000
    assert attributes["loop.usage.input_tokens"] == 500
    assert attributes["loop.usage.total_aiu"] == 2.0
    assert attributes["loop.usage.premium_requests"] == 1.0
    assert attributes["loop.agent.context_window"] == 200_000
    tool = capture.span("execute_tool bash")
    assert tool.parent.span_id == span.context.span_id
    assert tool.end_time - tool.start_time == 500_000_000
    assert capture.span("gate build").attributes["loop.gate.passed"] is True


def test_cli_is_run_in_the_repo_with_model_session_and_scoped_permissions(
        capture: Capture, tmp_path: Path) -> None:
    cli = FakeCli(tmp_path / "home")
    run, agent = agent_run(capture, cli, tmp_path)

    call(run, agent)

    (argv, cwd), = cli.calls
    assert cwd == tmp_path
    assert argv[:2] == ["copilot", "-p"] and argv[2].endswith("Do it")
    assert argv[argv.index("--model") + 1] == "claude-haiku-4.5"
    assert "--allow-all-tools" not in argv and "--allow-tool=shell(git:*)" in argv
    assert "--deny-tool=shell(git push)" in argv


def test_aiu_and_premium_requests_are_recorded_as_metrics(capture: Capture, tmp_path: Path) -> None:
    run, agent = agent_run(capture, FakeCli(tmp_path / "home"), tmp_path)

    call(run, agent)

    data = capture.metrics.get_metrics_data()
    names = {metric.name for rm in data.resource_metrics for sm in rm.scope_metrics
             for metric in sm.metrics}
    assert {"loop.agent.aiu", "loop.agent.premium_requests", "gen_ai.client.token.usage"} <= names


def test_reply_without_json_falls_back_to_the_scripted_envelope(capture: Capture, tmp_path: Path) -> None:
    run, agent = agent_run(capture, FakeCli(tmp_path / "home", reply="no json here"), tmp_path)

    assert call(run, agent) == {"status": "scripted"}


def test_failed_process_marks_the_agent_span_and_phase_as_failed(capture: Capture, tmp_path: Path) -> None:
    run, agent = agent_run(capture, FakeCli(tmp_path / "home", exit_code=1), tmp_path)

    with pytest.raises(AgentProcessError, match="exited 1: boom"):
        call(run, agent)

    assert capture.span("invoke_agent developer").status.status_code == StatusCode.ERROR
    assert capture.span("develop_1").status.status_code == StatusCode.ERROR


def test_missing_session_log_is_an_agent_failure(capture: Capture, tmp_path: Path) -> None:
    cli = FakeCli(tmp_path / "home")
    agent = CopilotAgent(CONFIG, "x", CopilotOptions(repo=tmp_path, home=tmp_path / "elsewhere", run=cli))
    run = Run(capture.telemetry, "e", "dev", speed=0, agents=lambda *_: agent)

    with pytest.raises(AgentProcessError, match="no readable session log"):
        call(run, agent)


@pytest.mark.parametrize("text, expected", [
    ('Done.\n```json\n{"status": "completed", "result": {"a": 1}}\n```', {"status": "completed", "result": {"a": 1}}),
    ('{"a": 1} then prose', None),
    (None, None)])
def test_envelope_is_the_json_object_that_ends_the_reply(text: str | None, expected: dict | None) -> None:
    assert _envelope(text) == expected
