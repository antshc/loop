"""A real coding agent: runs `copilot -p` in a repo, then reads its session log into the agent span."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from loop import Prompt

from .copilot_session import SessionRecord, read_session
from .fake_agent import record_gate, start_agent_span
from .model import AgentConfig, Attempt, GateFailure, GateReport
from .runner import PhaseHandle

DEFAULT_MODEL = "claude-haiku-4.5"
# Writes and git/python/pytest only; deny rules win over allows.
DEFAULT_ALLOW = ("write", "shell(git:*)", "shell(python:*)", "shell(python3:*)", "shell(pytest:*)")
DEFAULT_DENY = ("shell(git push)", "write(.env)")
_MAX_INSTRUCTION_CHARS = 8_000
_TIMEOUT_EXIT_CODE = 124


class AgentProcessError(Exception):
    """The Copilot CLI run failed or left no usable session log."""


@dataclass(frozen=True)
class ProcessResult:
    """What a finished `copilot` process left behind."""

    exit_code: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[list[str], Path, float], ProcessResult]


def run_process(argv: list[str], cwd: Path, timeout: float) -> ProcessResult:
    """Runs `argv` in `cwd`; a timeout is reported as exit code 124."""
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        return ProcessResult(_TIMEOUT_EXIT_CODE, "", f"timed out after {timeout:.0f}s")
    return ProcessResult(done.returncode, done.stdout, done.stderr)


def copilot_home() -> Path:
    """The directory holding `session-state/`."""
    return Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")


@dataclass(frozen=True)
class CopilotOptions:
    """How real agents are run; shared by every agent of one run."""

    repo: Path
    model: str = DEFAULT_MODEL
    timeout: float = 900.0
    allow: tuple[str, ...] = DEFAULT_ALLOW
    deny: tuple[str, ...] = DEFAULT_DENY
    home: Path = field(default_factory=copilot_home)
    run: ProcessRunner = run_process


class CopilotAgent:
    """Runs one Copilot CLI session per call; token, cost and tool data come from its log."""

    def __init__(self, config: AgentConfig, system_prompt: str, options: CopilotOptions) -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.options = options
        self.session_id = str(uuid.uuid4())

    def call(self, ph: PhaseHandle, prompt: Prompt, output_type: str, gate: str,
             attempts: Sequence[Attempt]) -> dict[str, object]:
        """Runs the CLI once and gates on the scripted checks of the first passing attempt."""
        user_prompt = prompt.render()
        started_at = time.perf_counter()
        span = start_agent_span(ph, self.config, self.session_id, self.system_prompt, user_prompt)
        with trace.use_span(span, end_on_exit=False):
            try:
                record = self._run(f"{self.system_prompt}\n\n{user_prompt}")
            except AgentProcessError as exception:
                span.set_attribute("error.type", "agent_process_failed")
                span.set_status(Status(StatusCode.ERROR, str(exception)[:1000]))
                span.end()
                raise
            self._tool_spans(ph, span, record)
            scripted = next((a for a in attempts if GateReport(a.checks).passed), attempts[-1])
            real = _envelope(record.output_text)
            envelope = real or scripted.envelope
            span.add_event("envelope", {
                "loop.envelope.output_type": output_type,
                "loop.envelope.payload": json.dumps(envelope),
                "loop.envelope.valid": real is not None, "loop.envelope.attempt": 0})
            passed = record_gate(ph, gate, GateReport(scripted.checks), 0)
        self._finish(ph, span, record, passed, time.perf_counter() - started_at)
        if not passed:
            raise GateFailure(f"{gate} failed")
        return envelope

    def _argv(self, prompt: str) -> list[str]:
        options = self.options
        return ["copilot", "-p", prompt, "--output-format", "json", "--no-color", "--no-ask-user",
                "--model", options.model, "--session-id", self.session_id,
                *[f"--allow-tool={tool}" for tool in options.allow],
                *[f"--deny-tool={tool}" for tool in options.deny]]

    def _run(self, prompt: str) -> SessionRecord:
        result = self.options.run(self._argv(prompt), self.options.repo, self.options.timeout)
        if result.exit_code != 0:
            raise AgentProcessError(
                f"copilot exited {result.exit_code}: {result.stderr.strip()[-500:]}")
        log = self.options.home / "session-state" / self.session_id / "events.jsonl"
        try:
            return read_session(log)
        except (OSError, ValueError, KeyError) as exception:
            raise AgentProcessError(f"no readable session log at {log}: {exception!r}") from exception

    def _tool_spans(self, ph: PhaseHandle, agent_span: Span, record: SessionRecord) -> None:
        for tool in record.tools:
            span = ph.tracer.start_span(
                f"execute_tool {tool.name}", context=trace.set_span_in_context(agent_span),
                kind=trace.SpanKind.INTERNAL, start_time=_nanos(tool.started_at),
                attributes={
                    "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool.name,
                    "gen_ai.tool.call.id": tool.call_id,
                    "gen_ai.tool.call.arguments": json.dumps(tool.arguments),
                    "gen_ai.tool.call.result": tool.result,
                    "gen_ai.agent.name": self.config.name, "loop.tool.ok": tool.ok})
            span.set_status(Status(StatusCode.OK if tool.ok else StatusCode.ERROR))
            span.end(end_time=_nanos(tool.ended_at))

    def _finish(self, ph: PhaseHandle, span: Span, record: SessionRecord, passed: bool,
                seconds: float) -> None:
        reason = "stop" if passed else "error"
        finish_reason = record.finish_reason or reason
        aiu = {f"loop.usage.{kind}_aiu": value for kind, value in record.aiu_by_type.items()}
        span.set_attributes({
            "gen_ai.response.model": record.response_model or self.config.model,
            "gen_ai.response.finish_reasons": [finish_reason],
            "gen_ai.output.messages": json.dumps([{
                "role": "assistant", "finish_reason": finish_reason,
                "parts": [{"type": "text", "content": record.output_text or ""}]}]),
            "gen_ai.system_instructions": json.dumps([{
                "type": "text",
                "content": (record.system_instructions or "")[:_MAX_INSTRUCTION_CHARS]}]),
            "gen_ai.usage.input_tokens": record.total_input_tokens,
            "gen_ai.usage.output_tokens": record.output_tokens,
            "loop.usage.input_tokens": record.input_tokens,
            "loop.usage.output_tokens": record.output_tokens,
            "loop.usage.reasoning_tokens": record.reasoning_tokens,
            "loop.usage.cache_read_tokens": record.cache_read_tokens,
            "loop.usage.cache_write_tokens": record.cache_write_tokens,
            "loop.usage.total_tokens": record.total_input_tokens + record.output_tokens,
            **aiu, "loop.usage.total_aiu": record.total_aiu,
            "loop.usage.premium_requests": record.premium_requests,
            "loop.agent.api_calls": record.api_calls, "loop.agent.cli_version": record.cli_version,
            **_optional("loop.agent.thinking", record.reasoning_effort),
            **_optional("loop.agent.context_window", record.context_window),
            **_optional("loop.agent.context_tokens", record.context_tokens)})
        span.set_status(Status(StatusCode.OK if passed else StatusCode.ERROR))
        span.end()
        self._record_metrics(ph, record, reason, seconds)

    def _record_metrics(self, ph: PhaseHandle, record: SessionRecord, reason: str,
                        seconds: float) -> None:
        telemetry = ph.run.telemetry
        common = {"gen_ai.operation.name": "invoke_agent",
                  "gen_ai.provider.name": self.config.coding_agent,
                  "gen_ai.request.model": self.config.model,
                  "gen_ai.agent.name": self.config.name}
        telemetry.token_usage.record(
            record.total_input_tokens, {**common, "gen_ai.token.type": "input"})
        telemetry.token_usage.record(
            record.output_tokens, {**common, "gen_ai.token.type": "output"})
        telemetry.operation_duration.record(
            seconds, {**common, **({} if reason == "stop" else {"error.type": "gate_failed"})})
        telemetry.aiu.add(record.total_aiu, common)
        telemetry.premium_requests.add(record.premium_requests, common)


def _optional(key: str, value: object) -> dict[str, object]:
    return {} if value is None else {key: value}


def _nanos(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _envelope(text: str | None) -> dict[str, object] | None:
    """The JSON object that ends `text`, if any; the prompts ask for one as the last thing said."""
    body = (text or "").rstrip().removesuffix("```").rstrip()
    decoder = json.JSONDecoder()
    for start, char in enumerate(body):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(body, start)
        except ValueError:
            continue
        if end == len(body) and isinstance(value, dict):
            return value
    return None
