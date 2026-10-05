from __future__ import annotations

import json
from pathlib import Path

from orb.contracts.execution_store import ExecutionStore


class FileExecutionStore(ExecutionStore):
    """Persists failed-attempt counts as JSON in a directory."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / "attempts.json"

    def failed_attempts(self, key: str) -> int:
        return self._load().get(key, 0)

    def record(self, key: str, success: bool) -> None:
        counts = self._load()
        counts[key] = 0 if success else counts.get(key, 0) + 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(counts, indent=2))

    def _load(self) -> dict[str, int]:
        if not self._path.is_file():
            return {}
        return json.loads(self._path.read_text())
