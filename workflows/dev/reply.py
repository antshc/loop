"""Content extraction and interpretation of dev agent responses."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .records import AgentCommitSubject, TicketResult as DevResult, TicketWorkflowError as DevError


class ContentDecodeError(Exception):
    """`text` carried no value this decoder recognizes."""

    def __init__(self, message: str, output: str) -> None:
        super().__init__(message)
        self.output = output


class LastJsonObject:
    """The last top-level JSON object in the text satisfying `where`; raises when none does."""

    def __init__(self, where: Callable[[dict], bool] = lambda _: True) -> None:
        self._where = where

    def decode(self, text: str) -> dict:
        decoder = json.JSONDecoder()
        found: dict | None = None
        index = text.find("{")
        while index != -1:
            try:
                value, end = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                index = text.find("{", index + 1)
                continue
            if isinstance(value, dict) and self._where(value):
                found = value
                index = text.find("{", end)
            else:
                index = text.find("{", index + 1)
        if found is None:
            raise ContentDecodeError("no matching JSON object in text", text)
        return found


class DevResultError(DevError):
    """The agent's response is missing, not valid JSON, or missing a required `result` field."""


def _check_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    identifier, status, result = envelope.get("identifier"), envelope.get("status"), envelope.get("result")
    if not isinstance(identifier, str) or status not in ("completed", "failed") or not isinstance(result, dict):
        raise DevResultError(f"malformed response envelope: {envelope!r}")
    return envelope


def _failed_result(identifier: str, result: dict[str, Any]) -> DevResult:
    reason = result.get("reason")
    if not isinstance(reason, str):
        raise DevResultError(f"failed response is missing result.reason: {result!r}")
    return DevResult(identifier, "failed", reason=reason)


def _completed_result(identifier: str, result: dict[str, Any]) -> DevResult:
    commit, summary, verification = result.get("commit"), result.get("summary"), result.get("verification")
    if not isinstance(commit, str) or not isinstance(summary, str) or not isinstance(verification, str):
        raise DevResultError(f"completed response is missing a required result field: {result!r}")
    return DevResult(identifier, "completed", commit=commit, summary=summary, verification=verification)


def parse_dev_result(envelope: dict[str, Any]) -> DevResult:
    """Decodes a run's response envelope for the `completed` and `failed` shapes `dev` understands."""
    envelope = _check_envelope(envelope)
    identifier, result = envelope["identifier"], envelope["result"]
    if envelope["status"] == "failed":
        return _failed_result(identifier, result)
    return _completed_result(identifier, result)


class DevResponse:
    """Decodes a run's response envelope for `identifier`; call `decode` directly on `AgentResult.output`."""

    def __init__(self, identifier: AgentCommitSubject) -> None:
        self._identifier = identifier
        self._envelope = LastJsonObject(lambda value: "status" in value)

    def decode(self, text: str) -> DevResult:
        """Decodes the last response object in `text` and checks it answers this task."""
        try:
            envelope = self._envelope.decode(text)
        except ContentDecodeError as exception:
            raise DevResultError("no response object in agent output") from exception
        if "identifier" in envelope and envelope["identifier"] != str(self._identifier):
            raise DevResultError(f"response identifier {envelope['identifier']!r} does not match task {self._identifier!r}")
        return parse_dev_result({**envelope, "identifier": str(self._identifier)})
