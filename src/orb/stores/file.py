from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from orb.contracts.agent_client import AgentSession, SessionStore
from orb.contracts.execution_store import ExecutionStore
from orb.errors import ExecutionStoreError

Clock = Callable[[], datetime]


class FileExecutionStore(ExecutionStore):
    """Daily JSON array of per-Spec execution records at `<directory>/<workflow>-execution-log-<UTC date>.json`."""

    def __init__(self, directory: Path, *, workflow: str = "dev", clock: Clock | None = None) -> None:
        self._directory = Path(directory)
        self._workflow = workflow
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def failed_attempts(self, key: str) -> int:
        record = self._find(self._load(), key)
        return record["count"] if record else 0

    def partial_count(self, key: str, ticket: int) -> int:
        record = self._find(self._load(), key)
        if record is None:
            return 0
        return record["partials"].get(str(ticket), 0)

    def record_failure(
        self,
        key: str,
        *,
        owner: str,
        repo: str,
        task_id: str,
        title: str,
        items: Sequence[int],
        partial_tickets: Sequence[int] = (),
        resolved_tickets: Sequence[int] = (),
    ) -> None:
        records = self._load()
        record = self._find(records, key)
        partials = dict(record["partials"]) if record else {}
        for ticket in partial_tickets:
            partials[str(ticket)] = partials.get(str(ticket), 0) + 1
        for ticket in resolved_tickets:
            partials.pop(str(ticket), None)
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
            "partials": partials,
        }
        if record is None:
            records.append(updated)
        else:
            records[records.index(record)] = updated
        self._save(records)

    def reset(self, key: str) -> None:
        records = [record for record in self._load() if record["task"] != key]
        self._save(records)

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


class FileSessionStore(SessionStore):
    """Persists logical-session metadata as JSON in a directory; never the transcript."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / "sessions.json"

    def get(self, key: str) -> AgentSession | None:
        stored = self._load().get(key)
        return None if stored is None else AgentSession(**stored)

    def save(self, session: AgentSession) -> None:
        sessions = self._load()
        sessions[session.key] = asdict(session)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(sessions, indent=2))

    def _load(self) -> dict[str, dict[str, str]]:
        if not self._path.is_file():
            return {}
        return json.loads(self._path.read_text())
