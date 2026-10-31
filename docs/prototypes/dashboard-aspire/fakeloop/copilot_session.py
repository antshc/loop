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
    # The `task` call that spawned the sub-agent which ran this tool; None for the lead agent.
    parent_call_id: str | None = None

    @property
    def seconds(self) -> float:
        """Wall time between the start and complete events; the CLI reports no duration."""
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class Usage:
    """Model calls summed over one agent."""

    calls: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    aiu_by_type: dict[str, float]
    total_aiu: float

    @property
    def total_input_tokens(self) -> int:
        """Input tokens as the semantic conventions count them: uncached plus cache reads and writes."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(frozen=True)
class SubagentRecord:
    """One sub-agent the lead spawned with the `task` tool, joined from its `subagent.*` events."""

    tool_call_id: str
    agent_id: str
    name: str
    agent_type: str
    description: str
    model: str | None
    reasoning_effort: str | None
    execution_mode: str
    prompt: str | None
    output_text: str | None
    ok: bool
    error: str | None
    total_tool_calls: int | None
    duration_ms: int | None
    started_at: datetime
    ended_at: datetime
    usage: Usage
    # The `task` call of the sub-agent that spawned this one; None when the lead did.
    parent_call_id: str | None = None


@dataclass(frozen=True)
class SessionRecord:
    """What the agent span and its tool spans need, read from one session.

    Token, cost and premium fields cover the lead agent only; its sub-agents' calls are in `subagents`.
    """

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
    subagents: list[SubagentRecord] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        """Input tokens as the semantic conventions count them: uncached plus cache reads and writes."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def read_session(events_path: Path) -> SessionRecord:
    """Parses a session's `events.jsonl`; usage comes from per-call records (CLI >= 1.0.95) or the shutdown summary."""
    events = [
        json.loads(line)
        for line in events_path.read_text().splitlines()
        if line.strip()
    ]
    by_type: dict[str, list[dict[str, object]]] = {}
    for event in events:
        by_type.setdefault(str(event["type"]), []).append(event)
    # Sub-agent events carry a top-level `agentId`; the lead agent's do not.
    lead = {
        kind: [e for e in found if "agentId" not in e]
        for kind, found in by_type.items()
    }
    start = by_type["session.start"][0]["data"]
    usage = _usage(
        by_type.get("session.usage_record", []),
        by_type.get("session.shutdown", []),
        None,
    )
    shutdown = by_type.get("session.shutdown", [{"data": {}}])[-1]["data"]
    messages = lead.get("assistant.message", [])
    records = lead.get("session.usage_record") or [{"data": {"usage": {}}}]
    last_record = records[-1]["data"]["usage"]
    return SessionRecord(
        session_id=start["sessionId"],
        cli_version=start["copilotVersion"],
        request_model=start.get("selectedModel") or _first_model_change(by_type),
        response_model=messages[-1]["data"].get("model") if messages else None,
        reasoning_effort=start.get("reasoningEffort")
        or last_record.get("reasoningEffort"),
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
        subagents=_subagents(events, by_type),
    )


def _first_model_change(by_type: dict[str, list[dict[str, object]]]) -> str | None:
    changes = by_type.get("session.model_change", [])
    return changes[0]["data"]["newModel"] if changes else None


def _first_content(events: list[dict[str, object]]) -> str | None:
    return events[0]["data"]["content"] if events else None


def _usage(
    records: list[dict[str, object]],
    shutdowns: list[dict[str, object]],
    agent_id: str | None,
) -> dict[str, object]:
    """Totals for the agent `agent_id` (None: the lead); only per-call records tell agents apart."""
    totals: dict[str, object] = {
        "calls": 0,
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
        "nano_aiu": 0.0,
        "premium": 0.0,
        "aiu_by_type": {},
    }
    if records:
        for record in records:
            if record.get("agentId") == agent_id:
                _add_call(totals, record["data"]["usage"])
    elif agent_id is None:
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
    completed = {
        e["data"]["toolCallId"]: e for e in by_type.get("tool.execution_complete", [])
    }
    records = []
    for start in by_type.get("tool.execution_start", []):
        data = start["data"]
        done = completed.get(data["toolCallId"])
        if done is None:
            continue
        result = done["data"].get("result") or {}
        records.append(
            ToolRecord(
                call_id=data["toolCallId"],
                name=data["toolName"],
                arguments=data.get("arguments", {}),
                result=result.get("content", ""),
                ok=bool(done["data"].get("success")),
                started_at=_when(start),
                ended_at=_when(done),
                parent_call_id=data.get("parentToolCallId"),
            )
        )
    return records


def _subagents(
    events: list[dict[str, object]], by_type: dict[str, list[dict[str, object]]]
) -> list[SubagentRecord]:
    configured = {
        e["agentId"]: e["data"] for e in by_type.get("subagent.configured", [])
    }
    finished = {
        e["data"]["toolCallId"]: e
        for kind in ("subagent.completed", "subagent.failed")
        for e in by_type.get(kind, [])
    }
    task_calls = {
        e["data"]["toolCallId"]: e["data"]
        for e in by_type.get("tool.execution_start", [])
    }
    usage_records = by_type.get("session.usage_record", [])
    records = []
    for start in by_type.get("subagent.started", []):
        data, agent_id = start["data"], start["agentId"]
        done = finished.get(data["toolCallId"])
        outcome = done["data"] if done else {}
        said = [
            e["data"]["content"]
            for e in by_type.get("assistant.message", [])
            if e.get("agentId") == agent_id and e["data"].get("content")
        ]
        call = task_calls.get(data["toolCallId"], {})
        ok = done is not None and done["type"] == "subagent.completed"
        # A sub-agent still running when the CLI exited (background mode) has no end event.
        error = (
            None if ok else str(outcome.get("error") or "no subagent.completed event")
        )
        records.append(
            SubagentRecord(
                tool_call_id=data["toolCallId"],
                agent_id=agent_id,
                name=data.get("agentDisplayName") or data["agentName"],
                agent_type=data.get("agentType") or data["agentName"],
                description=data.get("agentDescription", ""),
                model=data.get("model"),
                reasoning_effort=configured.get(agent_id, {}).get("reasoningEffort"),
                execution_mode=data.get("executionMode", "sync"),
                prompt=call.get("arguments", {}).get("prompt"),
                output_text=said[-1] if said else None,
                ok=ok,
                error=error,
                total_tool_calls=outcome.get("totalToolCalls"),
                duration_ms=outcome.get("durationMs"),
                started_at=_when(start),
                ended_at=_when(done if done else events[-1]),
                usage=_as_usage(_usage(usage_records, [], agent_id)),
                parent_call_id=call.get("parentToolCallId"),
            )
        )
    return records


def _as_usage(totals: dict[str, object]) -> Usage:
    return Usage(
        calls=totals["calls"],
        input_tokens=totals["input"] - totals["cache_read"] - totals["cache_write"],
        output_tokens=totals["output"],
        reasoning_tokens=totals["reasoning"],
        cache_read_tokens=totals["cache_read"],
        cache_write_tokens=totals["cache_write"],
        aiu_by_type=totals["aiu_by_type"],
        total_aiu=totals["nano_aiu"] / _NANO,
    )


def _when(event: dict[str, object]) -> datetime:
    # Python 3.10's fromisoformat needs exactly 3 or 6 fractional digits; the CLI sometimes writes 2.
    stamp = re.sub(
        r"\.(\d+)", lambda m: "." + m[1].ljust(6, "0")[:6], str(event["timestamp"])
    )
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def main(argv: list[str]) -> int:
    """Prints the record of the session log named by `argv[0]` as JSON."""
    if len(argv) != 1:
        print(
            "usage: python -m fakeloop.copilot_session <events.jsonl>", file=sys.stderr
        )
        return 2
    record = asdict(read_session(Path(argv[0])))
    for key in ("system_instructions", "output_text"):
        record[key] = (record[key] or "")[:120]
    record["input_messages"] = [message[:120] for message in record["input_messages"]]
    record["tools"] = [
        {
            "name": tool["name"],
            "ok": tool["ok"],
            "result": tool["result"][:80],
            "arguments": str(tool["arguments"])[:80],
            "parent_call_id": tool["parent_call_id"],
        }
        for tool in record["tools"]
    ]
    for subagent in record["subagents"]:
        subagent["prompt"] = (subagent["prompt"] or "")[:120]
        subagent["output_text"] = (subagent["output_text"] or "")[:120]
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
