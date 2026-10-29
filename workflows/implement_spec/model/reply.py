"""Content extraction and interpretation of implement_spec agent responses."""

from __future__ import annotations

from typing import Any

from workflows.shared.agents.envelope import ContentDecodeError, LastJsonObject

from .records import (
    AgentCommitSubject, ChoreResult, PlannedTicket, PlanResult,
    ReviewAxis, ReviewFinding, ReviewResult,
    TicketResult as DevResult, TicketWorkflowError as DevError,
)


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
    """Decodes a run's response envelope for the `completed` and `failed` shapes `implement_spec` understands."""
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


def _envelope(text: str) -> dict[str, Any]:
    """The last `{"status": ..., "result": {...}}` object in `text`."""
    try:
        envelope = LastJsonObject(lambda value: "status" in value).decode(text)
    except ContentDecodeError as exception:
        raise DevResultError("no response object in agent output") from exception
    if envelope["status"] not in ("completed", "failed") or not isinstance(envelope.get("result"), dict):
        raise DevResultError(f"malformed response envelope: {envelope!r}")
    return envelope


def _text(result: dict[str, Any], key: str, *, required: bool = False) -> str:
    value = result.get(key, "")
    if isinstance(value, str) and (value.strip() or not required):
        return value
    raise DevResultError(f"response is missing result.{key}: {result!r}")


def _items(result: dict[str, Any], key: str) -> list[Any]:
    value = result.get(key, [])
    if not isinstance(value, list):
        raise DevResultError(f"result.{key} must be a list: {result!r}")
    return value


def _failure(envelope: dict[str, Any]) -> str:
    reason = envelope["result"].get("reason")
    if not isinstance(reason, str):
        raise DevResultError(f"failed response is missing result.reason: {envelope['result']!r}")
    return reason


class PlanResponse:
    """Decodes the planner's `{"status", "result": {"tickets": [{"title", "body", "blocked_by"}]}}` object."""

    def decode(self, text: str) -> PlanResult:
        envelope = _envelope(text)
        if envelope["status"] == "failed":
            return PlanResult("failed", reason=_failure(envelope))
        tickets = []
        for item in _items(envelope["result"], "tickets"):
            if not isinstance(item, dict):
                raise DevResultError(f"planned ticket must be an object: {item!r}")
            blockers = item.get("blocked_by", [])
            if not isinstance(blockers, list) or not all(isinstance(blocker, int) and not isinstance(blocker, bool) for blocker in blockers):
                raise DevResultError(f"planned ticket blocked_by must be a list of indexes: {item!r}")
            tickets.append(PlannedTicket(_text(item, "title", required=True).strip(), _text(item, "body"), tuple(blockers)))
        return PlanResult("completed", tuple(tickets))


class ChoreResponse:
    """Decodes the chore agent's response; the step is best effort, so a missing or malformed response is an empty result."""

    def decode(self, text: str) -> ChoreResult:
        try:
            envelope = _envelope(text)
            if envelope["status"] == "failed":
                return ChoreResult()
            result = envelope["result"]
            out_of_scope = tuple(str(item) for item in _items(result, "out_of_scope"))
            return ChoreResult(_text(result, "commit_message"), _text(result, "summary"), out_of_scope)
        except DevResultError:
            return ChoreResult()


class ReviewResponse:
    """Decodes the reviewer's per-axis outcomes, findings, posted comment references and reviewed head."""

    def decode(self, text: str) -> ReviewResult:
        envelope = _envelope(text)
        if envelope["status"] == "failed":
            raise DevResultError(f"review failed: {_failure(envelope)}")
        result = envelope["result"]
        axes, findings = [], []
        for item in _items(result, "axes"):
            if not isinstance(item, dict):
                raise DevResultError(f"axis must be an object: {item!r}")
            axes.append(ReviewAxis(_text(item, "name", required=True), _text(item, "outcome", required=True).lower(), _text(item, "note")))
        for item in _items(result, "findings"):
            if not isinstance(item, dict):
                raise DevResultError(f"finding must be an object: {item!r}")
            findings.append(ReviewFinding(_text(item, "kind", required=True).lower(), _text(item, "summary", required=True), _text(item, "axis")))
        posted = tuple(str(item) for item in _items(result, "posted"))
        return ReviewResult(tuple(axes), tuple(findings), posted, _text(result, "head"))
