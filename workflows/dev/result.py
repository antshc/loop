"""The dev result model: decodes the agent's response envelope."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loop import LoopError
from workflows.platforms.work_tracking import WorkIdentifier


class DevResultError(LoopError):
    """The agent's response is missing, not valid JSON, or missing a required `result` field."""


@dataclass(frozen=True)
class DevResult:
    """The decoded `result` of a run's response envelope: `commit`/`summary`/`verification`, or `reason`."""

    identifier: str
    status: str
    commit: str | None = None
    summary: str | None = None
    verification: str | None = None
    reason: str | None = None


def _decode_envelope(response: str) -> dict[str, Any]:
    try:
        envelope = json.loads(response)
    except json.JSONDecodeError as exception:
        raise DevResultError(f"response is not valid JSON: {exception}") from exception
    if not isinstance(envelope, dict):
        raise DevResultError(f"response is not a JSON object: {response!r}")
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


def parse_dev_result(response: str) -> DevResult:
    """Decodes a run's response envelope for the `completed` and `failed` shapes `dev` understands."""
    envelope = _decode_envelope(response)
    identifier, result = envelope["identifier"], envelope["result"]
    if envelope["status"] == "failed":
        return _failed_result(identifier, result)
    return _completed_result(identifier, result)


def parse_response(response: str | None, identifier: WorkIdentifier) -> DevResult:
    """Decodes the agent's response and checks it answers task `identifier`."""
    if not response:
        raise DevResultError("no response object in agent output")
    dev_result = parse_dev_result(response)
    if dev_result.identifier != str(identifier):
        raise DevResultError(f"response identifier {dev_result.identifier!r} does not match task {identifier!r}")
    return dev_result
