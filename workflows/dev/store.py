"""Daily per-Ticket execution log of failed attempts."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .errors import ExecutionStoreError

Clock = Callable[[], datetime]


class FileExecutionStore:
    """Daily JSON array of per-Spec execution records at `<directory>/<workflow>-execution-log-<UTC date>.json`."""

    def __init__(self, directory: Path, *, workflow: str = "dev", clock: Clock | None = None) -> None:
        self._directory = Path(directory)
        self._workflow = workflow
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def failed_attempts(self, key: str) -> int:
        record = self._find(self._load(), key)
        return record["count"] if record else 0

    def record_failure(
        self,
        key: str,
        *,
        owner: str,
        repo: str,
        task_id: str,
        title: str,
        items: Sequence[int],
    ) -> None:
        records = self._load()
        record = self._find(records, key)
        updated = {
            "owner": owner,
            "repo": repo,
            "type": "spec",
            "task_id": task_id,
            "title": title,
            "task": key,
            "count": (record["count"] if record else 0) + 1,
            "last_run": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_items": list(items),
        }
        if record is None:
            records.append(updated)
        else:
            records[records.index(record)] = updated
        self._save(records)

    def reset(self, key: str) -> None:
        self._save([record for record in self._load() if record["task"] != key])

    def _path(self) -> Path:
        date = self._clock().strftime("%Y-%m-%d")
        return self._directory / f"{self._workflow}-execution-log-{date}.json"

    def _load(self) -> list[dict]:
        path = self._path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exception:
            raise ExecutionStoreError(f"execution log is not valid JSON: {path}") from exception
        if not isinstance(data, list):
            raise ExecutionStoreError(f"execution log must be a JSON array: {path}")
        return data

    def _save(self, records: list[dict]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, separators=(",", ":")))

    @staticmethod
    def _find(records: list[dict], key: str) -> dict | None:
        return next((record for record in records if record["task"] == key), None)
