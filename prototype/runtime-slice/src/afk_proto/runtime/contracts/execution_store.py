from __future__ import annotations

from abc import ABC, abstractmethod


class ExecutionStore(ABC):
    @abstractmethod
    def failed_attempts(self, key: str) -> int: ...

    @abstractmethod
    def record(self, key: str, success: bool) -> None: ...
