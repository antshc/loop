from __future__ import annotations

from collections.abc import Sequence

from loop.contracts.agent_client import AgentSession, SessionStore
from loop.contracts.execution_store import ExecutionStore


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def get(self, key: str) -> AgentSession | None:
        return self._sessions.get(key)

    def save(self, session: AgentSession) -> None:
        self._sessions[session.key] = session


class InMemoryExecutionStore(ExecutionStore):
    """In-memory ExecutionStore for tests that do not need the daily JSON file."""

    def __init__(self) -> None:
        self._records: dict[str, dict] = {}

    def failed_attempts(self, key: str) -> int:
        record = self._records.get(key)
        return record["count"] if record else 0

    def partial_count(self, key: str, ticket: int) -> int:
        record = self._records.get(key)
        return record["partials"].get(str(ticket), 0) if record else 0

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
        record = self._records.get(key)
        partials = dict(record["partials"]) if record else {}
        for ticket in partial_tickets:
            partials[str(ticket)] = partials.get(str(ticket), 0) + 1
        for ticket in resolved_tickets:
            partials.pop(str(ticket), None)
        self._records[key] = {
            "owner": owner,
            "repo": repo,
            "type": "spec",
            "task_id": task_id,
            "title": title,
            "task": key,
            "count": (record["count"] if record else 0) + 1,
            "last_items": list(items),
            "partials": partials,
        }

    def reset(self, key: str) -> None:
        self._records.pop(key, None)
