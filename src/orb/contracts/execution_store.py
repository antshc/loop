from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class ExecutionStore(ABC):
    """Consecutive failed-attempt and per-Ticket partial counts, keyed by the caller's own key."""

    @abstractmethod
    def failed_attempts(self, key: str) -> int: ...

    @abstractmethod
    def partial_count(self, key: str, ticket: int) -> int: ...

    @abstractmethod
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
    ) -> None: ...

    @abstractmethod
    def reset(self, key: str) -> None: ...
