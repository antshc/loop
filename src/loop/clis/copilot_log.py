from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from ..observe import LogReading, ToolCall, Usage
from ..sessions import SessionName, Start, Turn

_NANO = 1e9
# Fixed namespace so a session name always maps to the same Copilot session id.
_SESSION_NAMESPACE = uuid.UUID("6f1d2c3a-6c6f-4f70-8c6f-6f6f70000000")
_WINDOW_STARTS = ("session.start", "session.resume")


def copilot_home() -> Path:
    """The directory holding `session-state/`."""
    return Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")


def copilot_session_id(name: SessionName) -> str:
    """The Copilot session id a Loop session name is pinned to."""
    return str(uuid.uuid5(_SESSION_NAMESPACE, name.value))


class CopilotLogReader:
    """Pins a new Copilot session to a derivable id and reads its `events.jsonl` after each turn."""

    cli = "copilot"

    def __init__(self, home: Path | None = None) -> None:
        self._home = home

    def start_args(self, turn: Turn) -> tuple[str, ...]:
        """`--session-id` on a Start; a Resume reaches the pinned session through the saved name."""
        if isinstance(turn, Start):
            return ("--session-id", copilot_session_id(turn.name))
        return ()

    def read(self, turn: Turn) -> LogReading | None:
        """This turn's usage and tools; None when there is no log or it holds no per-call usage records."""
        session_id = copilot_session_id(turn.name)
        path = (self._home or copilot_home()) / "session-state" / session_id / "events.jsonl"
        if not path.is_file():
            return None
        return read_turn(session_id, _load(path))


def read_turn(session_id: str, events: list[dict[str, object]]) -> LogReading | None:
    """Reads the last turn of a session log: the events from its final `session.start` or `session.resume`."""
    window = _last_turn(events)
    records = [event["data"]["usage"] for event in window if event["type"] == "session.usage_record"]
    # CLIs before 1.0.95 write no per-call records; their shutdown totals are cumulative and cannot be sliced.
    if not records:
        return None
    messages = [event["data"] for event in window if event["type"] == "assistant.message"]
    start = window[0]["data"] if window[0]["type"] in _WINDOW_STARTS else {}
    return LogReading(
        native_id=session_id,
        model=(messages[-1].get("model") if messages else None) or records[-1].get("model") or start.get("selectedModel"),
        reasoning_effort=records[-1].get("reasoningEffort") or start.get("reasoningEffort"),
        usage=_usage(records),
        tools=_tools(window),
    )


def _load(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _last_turn(events: list[dict[str, object]]) -> list[dict[str, object]]:
    starts = [index for index, event in enumerate(events) if event["type"] in _WINDOW_STARTS]
    return events[starts[-1]:] if starts else events


def _usage(records: list[dict[str, object]]) -> Usage:
    aiu: dict[str, float] = {}
    nano = 0.0
    premium = 0.0
    for record in records:
        # Agent-initiated follow-up calls also report a cost, but only the user's prompt is a premium request.
        if record.get("initiator") == "user":
            premium += record.get("cost", 0)
        detail = record.get("copilotUsage", {})
        nano += detail.get("totalNanoAiu", 0)
        for item in detail.get("tokenDetails", []):
            batch = item["tokenCount"] * item["costPerBatch"] / item["batchSize"]
            aiu[item["tokenType"]] = aiu.get(item["tokenType"], 0.0) + batch / _NANO
    cache_read = sum(record["cacheReadTokens"] for record in records)
    cache_write = sum(record["cacheWriteTokens"] for record in records)
    return Usage(
        api_calls=len(records),
        input_tokens=sum(record["inputTokens"] for record in records) - cache_read - cache_write,
        output_tokens=sum(record["outputTokens"] for record in records),
        reasoning_tokens=sum(record.get("reasoningTokens", 0) for record in records),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        aiu=aiu,
        premium_requests=premium,
    )


def _tools(window: list[dict[str, object]]) -> tuple[ToolCall, ...]:
    completed = {event["data"]["toolCallId"]: event for event in window if event["type"] == "tool.execution_complete"}
    calls = []
    for start in (event for event in window if event["type"] == "tool.execution_start"):
        data = start["data"]
        done = completed.get(data["toolCallId"])
        if done is None:
            continue
        calls.append(
            ToolCall(
                call_id=data["toolCallId"],
                name=data["toolName"],
                arguments=data.get("arguments", {}),
                result=(done["data"].get("result") or {}).get("content", ""),
                ok=bool(done["data"].get("success")),
                started_at=_when(start),
                ended_at=_when(done),
            )
        )
    return tuple(calls)


def _when(event: dict[str, object]) -> datetime:
    # Python 3.10's fromisoformat needs exactly 3 or 6 fractional digits; the CLI sometimes writes 2.
    stamp = re.sub(r"\.(\d+)", lambda match: "." + match[1].ljust(6, "0")[:6], str(event["timestamp"]))
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
