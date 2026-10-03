from __future__ import annotations

from ship_proto.runtime.contracts.execution_store import ExecutionStore

MAX_FAILED_ATTEMPTS = 3


def may_attempt(store: ExecutionStore, key: str) -> bool:
    return store.failed_attempts(key) < MAX_FAILED_ATTEMPTS
