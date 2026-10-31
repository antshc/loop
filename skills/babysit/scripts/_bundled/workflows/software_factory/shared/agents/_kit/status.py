"""Status-envelope helpers every `completed | failed` role decodes with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loop import Declined, ReplyMismatch

from .envelope import ContentDecodeError, LastJsonObject

_STATUSES = ("completed", "failed")


@dataclass(frozen=True)
class StatusEnvelope:
    """The last `{"status", "result"}` object of an agent's output."""

    status: str
    result: dict[str, Any]

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def declined(self) -> Declined:
        """The reply of a `failed` envelope; its reason is required."""
        reason = self.result.get("reason")
        if not isinstance(reason, str):
            raise ReplyMismatch(
                f"failed response is missing result.reason: {self.result!r}"
            )
        return Declined(reason)

    def text(self, key: str, *, required: bool = False) -> str:
        return text(self.result, key, required=required)

    def items(self, key: str) -> list[Any]:
        return items(self.result, key)


def read_status(output: str, *, identifier: str | None = None) -> StatusEnvelope:
    """The envelope in `output`; an `identifier` the envelope echoes must equal the request's, an absent one is accepted."""
    try:
        envelope = LastJsonObject(lambda value: "status" in value).decode(output)
    except ContentDecodeError as exception:
        raise ReplyMismatch("no response object in agent output") from exception
    if identifier is not None and envelope.get("identifier", identifier) != identifier:
        raise ReplyMismatch(
            f"response identifier {envelope['identifier']!r} does not match task {identifier!r}"
        )
    if envelope["status"] not in _STATUSES or not isinstance(
        envelope.get("result"), dict
    ):
        raise ReplyMismatch(f"malformed response envelope: {envelope!r}")
    return StatusEnvelope(envelope["status"], envelope["result"])


def text(result: dict[str, Any], key: str, *, required: bool = False) -> str:
    """`result[key]` as a string; empty when absent unless `required`."""
    value = result.get(key, "")
    if isinstance(value, str) and (value.strip() or not required):
        return value
    raise ReplyMismatch(f"response is missing result.{key}: {result!r}")


def items(result: dict[str, Any], key: str) -> list[Any]:
    """`result[key]` as a list; empty when absent."""
    value = result.get(key, [])
    if not isinstance(value, list):
        raise ReplyMismatch(f"result.{key} must be a list: {result!r}")
    return value


def objects(result: dict[str, Any], key: str, what: str) -> list[dict[str, Any]]:
    """`result[key]` as a list of JSON objects, each named `what` in the error."""
    found = items(result, key)
    for item in found:
        if not isinstance(item, dict):
            raise ReplyMismatch(f"{what} must be an object: {item!r}")
    return found
