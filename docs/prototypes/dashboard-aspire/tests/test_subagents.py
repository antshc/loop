from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.trace import StatusCode

import run_fake
from conftest import Capture, event, write_log
from fakeloop.copilot_agent import CopilotAgent, CopilotOptions, ProcessResult
from fakeloop.copilot_session import read_session
from fakeloop.runner import Run
from fakeloop.scenario import ScenarioOptions
from fakeloop.subagents import run_subagents


def tagged(item: dict[str, object], agent_id: str, parent_call: str) -> dict[str, object]:
    """Marks an event as one a sub-agent produced, the way the CLI does."""
    item["agentId"] = agent_id
    item["data"]["parentToolCallId"] = parent_call
    return item


def usage(model: str, initiator: str, inputs: int, output: int, when: str,
          agent_id: str | None = None) -> dict[str, object]:
    record = event("session.usage_record", when, usage={
        "model": model, "inputTokens": inputs, "outputTokens": output, "cacheReadTokens": 0,
        "cacheWriteTokens": 0, "cost": 1.0, "initiator": initiator, "finishReason": "stop",
        "maxPromptTokens": 200_000, "copilotUsage": {"totalNanoAiu": 1_000_000_000, "tokenDetails": []}})
    if agent_id:
        record["agentId"] = agent_id
    return record


def subagent_log(session_id: str) -> list[dict[str, object]]:
    """A lead that spawns two sub-agents in parallel; the explorer views a file, the other runs a command."""
    explorer, runner = "agent-explore", "agent-task"
    return [
        event("session.start", "00.000", sessionId=session_id, copilotVersion="1.0.103",
              selectedModel="claude-haiku-4.5"),
        event("user.message", "00.100", content="delegate"),
        event("tool.execution_start", "01.000", toolCallId="c1", toolName="task",
              arguments={"name": "code-reader", "agent_type": "explore", "prompt": "read basket.py"}),
        event("tool.execution_start", "01.010", toolCallId="c2", toolName="task",
              arguments={"name": "test-runner", "agent_type": "task", "prompt": "run pytest"}),
        {**event("subagent.started", "01.100", toolCallId="c1", agentName="explore",
                 agentDisplayName="code-reader", agentDescription="Read the basket",
                 model="claude-haiku-4.5", agentType="explore", executionMode="sync"),
         "agentId": explorer},
        {**event("subagent.started", "01.110", toolCallId="c2", agentName="task",
                 agentDisplayName="test-runner", agentDescription="Run the tests",
                 model="claude-haiku-4.5", agentType="task", executionMode="sync"),
         "agentId": runner},
        {**event("subagent.configured", "01.200", reasoningEffort="low"), "agentId": explorer},
        tagged(event("tool.execution_start", "02.000", toolCallId="v1", toolName="view",
                     arguments={"path": "basket.py"}), explorer, "c1"),
        tagged(event("tool.execution_complete", "02.500", toolCallId="v1", success=True,
                     result={"content": "class Basket"}), explorer, "c1"),
        tagged(event("tool.execution_start", "02.100", toolCallId="b1", toolName="bash",
                     arguments={"command": "python3 -m pytest -q"}), runner, "c2"),
        tagged(event("tool.execution_complete", "04.100", toolCallId="b1", success=False,
                     result={"content": "1 failed"}), runner, "c2"),
        tagged(event("assistant.message", "04.200", content="Basket has add and total.",
                     model="claude-haiku-4.5"), explorer, "c1"),
        usage("claude-haiku-4.5", "sub-agent", 700, 30, "04.300", explorer),
        usage("claude-haiku-4.5", "sub-agent", 900, 20, "04.400", runner),
        {**event("subagent.completed", "04.500", toolCallId="c1", totalToolCalls=1, durationMs=3_400),
         "agentId": explorer},
        {**event("subagent.failed", "05.000", toolCallId="c2", error="tests failed"), "agentId": runner},
        event("tool.execution_complete", "05.100", toolCallId="c1", success=True, result={"content": "ok"}),
        event("tool.execution_complete", "05.110", toolCallId="c2", success=False, result={"content": "no"}),
        usage("claude-haiku-4.5", "user", 1_000, 40, "05.200"),
        event("assistant.message", "05.300", content='Done. {"status": "success", "summary": "s"}',
              model="claude-haiku-4.5"),
    ]


class SubagentCli:
    """A fake `copilot` that leaves the sub-agent session log."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def __call__(self, argv: list[str], cwd: Path, timeout: float) -> ProcessResult:
        session_id = argv[argv.index("--session-id") + 1]
        write_log(self.home / "session-state" / session_id / "events.jsonl", subagent_log(session_id))
        return ProcessResult(0, "", "")


def test_reader_separates_subagents_from_the_lead(tmp_path: Path) -> None:
    write_log(tmp_path / "events.jsonl", subagent_log("s1"))

    record = read_session(tmp_path / "events.jsonl")

    assert (record.input_tokens, record.output_tokens, record.api_calls) == (1_000, 40, 1)
    assert record.output_text.startswith("Done.")
    explorer, runner = record.subagents
    assert (explorer.name, explorer.agent_type, explorer.ok, explorer.reasoning_effort) == (
        "code-reader", "explore", True, "low")
    assert (explorer.usage.input_tokens, explorer.usage.output_tokens) == (700, 30)
    assert (explorer.prompt, explorer.output_text) == ("read basket.py", "Basket has add and total.")
    assert (explorer.total_tool_calls, explorer.duration_ms) == (1, 3_400)
    assert (runner.ok, runner.error) == (False, "tests failed")
    assert {tool.call_id: tool.parent_call_id for tool in record.tools} == {
        "c1": None, "c2": None, "v1": "c1", "b1": "c2"}


def test_subagent_spans_nest_under_their_task_call(capture: Capture, tmp_path: Path) -> None:
    options = CopilotOptions(repo=tmp_path, home=tmp_path / "home", run=SubagentCli(tmp_path / "home"))
    run = Run(capture.telemetry, "e", "subagents", speed=0,
              agents=lambda config, prompt: CopilotAgent(config, prompt, options))

    run_subagents(run, ScenarioOptions("go", repo=None))
    run.finish()

    spans = capture.spans.get_finished_spans()
    by_id = {span.context.span_id: span for span in spans}

    def parent(name: str) -> str:
        return by_id[capture.span(name).parent.span_id].name

    lead = capture.span("invoke_agent lead")
    assert parent("invoke_agent code-reader") == parent("invoke_agent test-runner") == "execute_tool task"
    tasks = [span for span in spans if span.name == "execute_tool task"]
    assert {by_id[task.parent.span_id].name for task in tasks} == {"invoke_agent lead"}
    assert parent("execute_tool view") == "invoke_agent code-reader"
    assert parent("execute_tool bash") == "invoke_agent test-runner"
    assert lead.attributes["loop.agent.subagents"] == 2
    assert lead.attributes["gen_ai.usage.output_tokens"] == 40

    explorer = capture.span("invoke_agent code-reader")
    assert explorer.attributes["loop.agent.is_subagent"] is True
    assert explorer.attributes["gen_ai.usage.input_tokens"] == 700
    assert explorer.attributes["loop.agent.parent"] == "lead"
    assert explorer.status.status_code == StatusCode.OK
    assert capture.span("invoke_agent test-runner").status.status_code == StatusCode.ERROR
    assert capture.span("execute_tool view").attributes["gen_ai.agent.name"] == "code-reader"


def test_subagent_usage_is_recorded_once_in_the_metrics(capture: Capture, tmp_path: Path) -> None:
    options = CopilotOptions(repo=tmp_path, home=tmp_path / "home", run=SubagentCli(tmp_path / "home"))
    run = Run(capture.telemetry, "e", "subagents", speed=0,
              agents=lambda config, prompt: CopilotAgent(config, prompt, options))

    run_subagents(run, ScenarioOptions("go", repo=None))

    tokens: dict[str, int] = {}
    for resource in capture.metrics.get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == "gen_ai.client.token.usage":
                    for point in metric.data.data_points:
                        agent = point.attributes["gen_ai.agent.name"]
                        tokens[agent] = tokens.get(agent, 0) + int(point.sum)
    assert tokens == {"lead": 1_040, "code-reader": 730, "test-runner": 920}


def test_subagents_workflow_only_runs_real_agents(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_fake.main(["--workflow", "subagents", "--dry"]) == 2
    assert "no fake" in capsys.readouterr().err
