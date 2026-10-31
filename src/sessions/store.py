from __future__ import annotations

from typing import Protocol

from .values import NativeHandle, SessionName


class SessionStore(Protocol):
    def get(self, name: SessionName, cli: str) -> NativeHandle | None: ...

    def save(self, name: SessionName, handle: NativeHandle) -> None: ...


class MemorySessionStore:
    """Process-local session store; sessions are not persisted across runs of the process."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[SessionName, str], NativeHandle] = {}

    def get(self, name: SessionName, cli: str) -> NativeHandle | None:
        return self._sessions.get((name, cli))

    def save(self, name: SessionName, handle: NativeHandle) -> None:
        self._sessions[(name, handle.cli)] = handle
