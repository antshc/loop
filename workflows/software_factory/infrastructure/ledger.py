"""Factory ledger reads retain GitHub values recorded before their package moved."""

from __future__ import annotations

from typing import Any

from loop import Ledger as LoopLedger
from loop import WorkItemLog

_OLD = "loop.shared.github."
_NEW = "workflows.software_factory.infrastructure.github."


class Ledger(LoopLedger):
    """Reads old GitHub type tags without changing the append-only log on disk."""

    def item(self, workflow: str, item: str) -> WorkItemLog:
        """The work item's log with relocated GitHub type tags normalized on read."""
        return _GitHubLog(super().item(workflow, item).path.parent)


class _GitHubLog(WorkItemLog):
    def _read(self) -> list[dict[str, Any]]:
        return [_relocated(record) for record in super()._read()]


def _relocated(value: Any) -> Any:
    if isinstance(value, list):
        return [_relocated(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _relocated(item) for key, item in value.items()}
    tag = result.get("type")
    if (
        isinstance(result.get("fields"), dict)
        and isinstance(tag, str)
        and tag.startswith(_OLD)
    ):
        result["type"] = _NEW + tag.removeprefix(_OLD)
    return result
