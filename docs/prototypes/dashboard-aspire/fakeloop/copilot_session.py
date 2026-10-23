"""Reads one real Copilot CLI session log into the fields `FakeAgent` fakes: `python -m fakeloop.copilot_session <events.jsonl>`."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

_NANO = 1e9


@dataclass(frozen=True)
class ToolRecord:
    """One tool call joined from `tool.execution_start` and `tool.execution_complete`."""

    call_id: str
    name: str
    arguments: dict[str, object]
    result: str
    ok: bool
    started_at: datetime
    ended_at: datetime

    @property
    def seconds(self) -> float:
        """Wall time between the start and complete events; the CLI reports no duration."""
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class SessionRecord:
    """What the agent span and its tool spans need, read from one session."""

    session_id: str
    cli_version: str
    request_model: str | None
    response_model: str | None
    reasoning_effort: str | None
    system_instructions: str | None
    input_messages: list[str]
    output_text: str | None
    finish_reason: str | None
    api_calls: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    aiu_by_type: dict[str, float]
    total_aiu: float
    premium_requests: float
    context_window: int | None
    context_tokens: int | None
    tools: list[ToolRecord] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        """Input tokens as the semantic conventions count them: uncached plus cache reads and writes."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def read_session(events_path: Path) -> SessionRecord:
    """Parses a session's `events.jsonl`; usage comes from per-call records (CLI >= 1.0.95) or the shutdown summary."""
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    by_type: dict[str, list[dict[str, object]]] = {}
    for event in events:
        by_type.setdefault(str(event["type"]), []).append(event)
    start = by_type["session.start"][0]["data"]
    usage = _usage(by_type.get("session.usage_record", []), by_type.get("session.shutdown", []))
    shutdown = by_type.get("session.shutdown", [{"data": {}}])[-1]["data"]
    messages = by_type.get("assistant.message", [])
    last_record = by_type.get("session.usage_record", [{"data": {"usage": {}}}])[-1]["data"]["usage"]
    return SessionRecord(
        session_id=start["sessionId"],
        cli_version=start["copilotVersion"],
        request_model=start.get("selectedModel") or _first_model_change(by_type),
        response_model=messages[-1]["data"].get("model") if messages else None,
        reasoning_effort=start.get("reasoningEffort") or last_record.get("reasoningEffort"),
        system_instructions=_first_content(by_type.get("system.message", [])),
        input_messages=[m["data"]["content"] for m in by_type.get("user.message", [])],
        output_text=messages[-1]["data"].get("content") if messages else None,
        finish_reason=last_record.get("finishReason"),
        api_calls=usage["calls"],
        input_tokens=usage["input"] - usage["cache_read"] - usage["cache_write"],
        output_tokens=usage["output"],
        reasoning_tokens=usage["reasoning"],
        cache_read_tokens=usage["cache_read"],
        cache_write_tokens=usage["cache_write"],
        aiu_by_type=usage["aiu_by_type"],
        total_aiu=usage["nano_aiu"] / _NANO,
        premium_requests=usage["premium"],
        context_window=last_record.get("maxPromptTokens"),
        context_tokens=shutdown.get("currentTokens"),
        tools=_tools(by_type),
    )


def _first_model_change(by_type: dict[str, list[dict[str, object]]]) -> str | None:
    changes = by_type.get("session.model_change", [])
    return changes[0]["data"]["newModel"] if changes else None


def _first_content(events: list[dict[str, object]]) -> str | None:
    return events[0]["data"]["content"] if events else None


def _usage(records: list[dict[str, object]], shutdowns: list[dict[str, object]]) -> dict[str, object]:
    totals: dict[str, object] = {
        "calls": 0, "input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
        "nano_aiu": 0.0, "premium": 0.0, "aiu_by_type": {}}
    if records:
        for record in records:
            _add_call(totals, record["data"]["usage"])
    else:
        for shutdown in shutdowns:
            _add_shutdown(totals, shutdown["data"])
    return totals


def _add_call(totals: dict[str, object], call: dict[str, object]) -> None:
    totals["calls"] += 1
    totals["input"] += call["inputTokens"]
    totals["output"] += call["outputTokens"]
    totals["reasoning"] += call.get("reasoningTokens", 0)
    totals["cache_read"] += call["cacheReadTokens"]
    totals["cache_write"] += call["cacheWriteTokens"]
    # Agent-initiated follow-up calls also report cost 1.0, but only the user's prompt is a premium request.
    if call.get("initiator") == "user":
        totals["premium"] += call.get("cost", 0)
    detail = call.get("copilotUsage", {})
    totals["nano_aiu"] += detail.get("totalNanoAiu", 0)
    for item in detail.get("tokenDetails", []):
        nano = item["tokenCount"] * item["costPerBatch"] / item["batchSize"]
        by_type = totals["aiu_by_type"]
        by_type[item["tokenType"]] = by_type.get(item["tokenType"], 0.0) + nano / _NANO


def _add_shutdown(totals: dict[str, object], summary: dict[str, object]) -> None:
    for metrics in summary.get("modelMetrics", {}).values():
        usage = metrics["usage"]
        totals["calls"] += metrics["requests"]["count"]
        totals["input"] += usage["inputTokens"]
        totals["output"] += usage["outputTokens"]
        totals["reasoning"] += usage.get("reasoningTokens", 0)
        totals["cache_read"] += usage["cacheReadTokens"]
        totals["cache_write"] += usage["cacheWriteTokens"]
    totals["nano_aiu"] += summary.get("totalNanoAiu", 0)
    totals["premium"] += summary.get("totalPremiumRequests", 0)


def _tools(by_type: dict[str, list[dict[str, object]]]) -> list[ToolRecord]:
    completed = {e["data"]["toolCallId"]: e for e in by_type.get("tool.execution_complete", [])}
    records = []
    for start in by_type.get("tool.execution_start", []):
        data = start["data"]
        done = completed.get(data["toolCallId"])
        if done is None:
            continue
        result = done["data"].get("result") or {}
        records.append(ToolRecord(
            call_id=data["toolCallId"], name=data["toolName"], arguments=data.get("arguments", {}),
            result=result.get("content", ""), ok=bool(done["data"].get("success")),
            started_at=_when(start), ended_at=_when(done)))
    return records


def _when(event: dict[str, object]) -> datetime:
    # Python 3.10's fromisoformat needs exactly 3 or 6 fractional digits; the CLI sometimes writes 2.
    stamp = re.sub(r"\.(\d+)", lambda m: "." + m[1].ljust(6, "0")[:6], str(event["timestamp"]))
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def main(argv: list[str]) -> int:
    """Prints the record of the session log named by `argv[0]` as JSON."""
    if len(argv) != 1:
        print("usage: python -m fakeloop.copilot_session <events.jsonl>", file=sys.stderr)
        return 2
    record = asdict(read_session(Path(argv[0])))
    for key in ("system_instructions", "output_text"):
        record[key] = (record[key] or "")[:120]
    record["input_messages"] = [message[:120] for message in record["input_messages"]]
    record["tools"] = [{"name": tool["name"], "ok": tool["ok"], "result": tool["result"][:80],
                        "arguments": str(tool["arguments"])[:80]} for tool in record["tools"]]
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
