"""Shared fixtures: in-memory telemetry and a builder for Copilot session logs."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakeloop import telemetry as telemetry_module  # noqa: E402


@dataclass
class Capture:
    """What a run exported."""

    telemetry: telemetry_module.Telemetry
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader

    def span(self, name: str):
        """The one finished span called `name`."""
        found = [span for span in self.spans.get_finished_spans() if span.name == name]
        assert len(found) == 1, f"{name}: {len(found)} spans"
        return found[0]


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Capture:
    spans, metrics = InMemorySpanExporter(), InMemoryMetricReader()
    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", lambda: spans)
    monkeypatch.setattr(telemetry_module, "OTLPLogExporter", InMemoryLogExporter)
    monkeypatch.setattr(telemetry_module, "OTLPMetricExporter", lambda: None)
    monkeypatch.setattr(telemetry_module, "PeriodicExportingMetricReader",
                        lambda *args, **kwargs: metrics)
    return Capture(telemetry_module.Telemetry(), spans, metrics)


def event(kind: str, when: str, **data: object) -> dict[str, object]:
    """One `events.jsonl` line."""
    return {"type": kind, "data": data, "timestamp": f"2026-10-23T13:00:{when}Z"}


def write_log(path: Path, events: list[dict[str, object]]) -> None:
    """Writes `events` as the session log at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n")


def per_call_log(session_id: str, reply: str = 'done {"status": "completed"}') -> list[dict[str, object]]:
    """A CLI >= 1.0.95 log: one tool call over two model calls, user call first."""
    def usage(initiator: str, inputs: int, cache_read: int, cache_write: int, output: int) -> dict:
        details = [
            {"tokenType": "input", "tokenCount": inputs - cache_read - cache_write,
             "costPerBatch": 200_000_000_000, "batchSize": 1_000_000},
            {"tokenType": "output", "tokenCount": output,
             "costPerBatch": 1_000_000_000_000, "batchSize": 1_000_000}]
        return {"usage": {
            "model": "claude-haiku-4.5", "inputTokens": inputs, "outputTokens": output,
            "cacheReadTokens": cache_read, "cacheWriteTokens": cache_write, "reasoningTokens": 5,
            "cost": 1.0, "initiator": initiator, "finishReason": "stop",
            "reasoningEffort": "medium", "maxPromptTokens": 200_000,
            "copilotUsage": {"tokenDetails": details, "totalNanoAiu": 1_000_000_000}}}

    return [
        event("session.start", "00.000", sessionId=session_id, copilotVersion="1.0.103",
              selectedModel="claude-haiku-4.5"),
        event("system.message", "00.100", content="You are the CLI."),
        event("user.message", "00.200", content="do the thing"),
        event("tool.execution_start", "01.000", toolCallId="t1", toolName="bash",
              arguments={"command": "git status"}),
        event("tool.execution_complete", "01.500", toolCallId="t1", success=True,
              result={"content": "clean"}),
        event("session.usage_record", "02.000", **usage("user", 1_000, 600, 300, 40)),
        event("session.usage_record", "03.000", **usage("agent", 2_000, 1_500, 100, 60)),
        event("assistant.message", "03.100", content=reply, model="claude-haiku-4.5"),
    ]
