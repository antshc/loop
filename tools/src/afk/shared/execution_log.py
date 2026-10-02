"""ExecutionLog: reads/writes per-PR attempt counts to cap retries."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from modules.github.domain.execution_record import ExecutionRecord

_log = logging.getLogger(__name__)


class ExecutionLog:
    def __init__(self, log_dir: Path, repo: str, log_name: str):
        self._log_dir = log_dir
        self._log_name = log_name
        self._log: list[dict] = []
        self._init()

    def _log_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._log_dir / f"{self._log_name}-execution-log-{today}.json"

    def _init(self) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_path()
        if not path.exists():
            path.write_text("[]")
        payload = json.loads(path.read_text())
        if not isinstance(payload, list):
            raise ValueError("Execution log must be a JSON array of records")
        self._log = payload

    def _save(self) -> None:
        self._log_path().write_text(f"{json.dumps(self._log, separators=(',', ':'))}\n\n")

    def _find_entry_index(self, task: str) -> int | None:
        for index, entry in enumerate(self._log):
            if entry.get("task") == task:
                return index
        return None

    def get_count(self, task: str) -> int:
        index = self._find_entry_index(task)
        if index is None:
            return 0
        return int(self._log[index].get("count", 0))

    def update(
        self,
        task: str,
        item_ids: list[str | int],
        owner: str,
        repo: str,
        type: str,
        id: str | int,
        title: str = "",
    ) -> None:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        index = self._find_entry_index(task)
        entry = self._log[index] if index is not None else {}
        record = ExecutionRecord(
            task=task,
            count=entry.get("count", 0) + 1,
            last_run=ts,
            last_items=item_ids,
        )
        persisted = {
            "owner": owner,
            "repo": repo,
            "type": type,
            "task_id": str(id),
            "title": title,
            "task": record.task,
            "count": record.count,
            "last_run": record.last_run,
            "last_items": record.last_items,
        }
        if index is None:
            self._log.append(persisted)
        else:
            self._log[index] = persisted
        self._save()
        _log.info(
            "execution log persisted",
            extra={
                "owner": owner,
                "repo": repo,
                "type": type,
                "task_id": str(id),
                "title": title,
                "task": record.task,
                "count": record.count,
                "last_run": record.last_run,
                "last_items": record.last_items,
            },
        )

    def reset(self, task: str) -> None:
        self._log = [entry for entry in self._log if entry.get("task") != task]
        self._save()
