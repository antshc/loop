"""Read persisted factory type tags across package relocations."""

from __future__ import annotations

from typing import Any

from loop import Ledger as LoopLedger
from loop import WorkItemLog

_FACTORY = "workflows.software_factory."
_SHARED = f"{_FACTORY}shared."
_OLD_GITHUB = "loop.shared.github."
_RELOCATED_PACKAGES = frozenset(
    {"agents", "events", "flows", "infrastructure", "stages"}
)


class Ledger(LoopLedger):
    """Reads old type tags without changing the append-only log on disk."""

    def item(self, workflow: str, item: str) -> WorkItemLog:
        """The work item's log with relocated type tags normalized on read."""
        return _RelocatedLog(super().item(workflow, item).path.parent)


class _RelocatedLog(WorkItemLog):
    def _read(self) -> list[dict[str, Any]]:
        return [_relocated(record) for record in super()._read()]


def _relocated(value: Any) -> Any:
    if isinstance(value, list):
        return [_relocated(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _relocated(item) for key, item in value.items()}
    tag = result.get("type")
    if isinstance(result.get("fields"), dict) and isinstance(tag, str):
        if tag.startswith(_OLD_GITHUB):
            result["type"] = (
                f"{_SHARED}infrastructure.github.{tag.removeprefix(_OLD_GITHUB)}"
            )
        elif tag.startswith(_FACTORY) and not tag.startswith(_SHARED):
            package = tag.removeprefix(_FACTORY).partition(".")[0]
            if package in _RELOCATED_PACKAGES:
                result["type"] = _SHARED + tag.removeprefix(_FACTORY)
    return result
