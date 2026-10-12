"""Per-CLI stdout transports: decode a CLI's stdout into the assistant text, and encode it back for dry run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Transcript:
    """One decoded turn: the assistant's text, the handle the stream carried (if any), and its reported exit code."""

    text: str
    handle_value: str | None
    exit_code: int | None


class Transport(Protocol):
    """Adapter for one CLI's stdout shape; pure and total, so dry run can round-trip through it."""

    args: tuple[str, ...]

    def decode(self, stdout: str) -> Transcript:
        """Unreadable lines are skipped."""
        ...

    def encode(self, text: str, handle_value: str | None) -> str:
        ...


class CopilotText:
    """`copilot --output-format text`: stdout is already the assistant's text."""

    args: tuple[str, ...] = ("--output-format", "text")

    def decode(self, stdout: str) -> Transcript:
        return Transcript(stdout, None, None)

    def encode(self, text: str, handle_value: str | None) -> str:
        return text


class CopilotJson:
    """`copilot --output-format json`: one JSON event per line, per copilot-cli-json-output-events.md."""

    args: tuple[str, ...] = ("--output-format", "json")

    def decode(self, stdout: str) -> Transcript:
        parts: list[str] = []
        session_id: str | None = None
        exit_code: int | None = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "assistant.message_delta":
                parts.append(event.get("data", {}).get("deltaContent", ""))
            elif event.get("type") == "result":
                session_id = event.get("sessionId")
                exit_code = event.get("exitCode")
        return Transcript("".join(parts), session_id, exit_code)

    def encode(self, text: str, handle_value: str | None) -> str:
        delta = {"type": "assistant.message_delta", "data": {"messageId": "dry-run", "deltaContent": text}}
        result = {"type": "result", "sessionId": handle_value, "exitCode": 0}
        return "\n".join(json.dumps(event) for event in (delta, result))


class CodexJson:
    """`codex exec --json`: one JSON event per line; Codex assigns its own thread id via `thread.started`."""

    args: tuple[str, ...] = ("--json",)

    def decode(self, stdout: str) -> Transcript:
        thread_id: str | None = None
        message = ""
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            elif event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                message = event["item"].get("text", "")
        return Transcript(message, thread_id, None)

    def encode(self, text: str, handle_value: str | None) -> str:
        started = {"type": "thread.started", "thread_id": handle_value}
        completed = {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
        return "\n".join(json.dumps(event) for event in (started, completed))
