from __future__ import annotations

from orb.contracts.agent_client import AgentSession, SessionStore


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def get(self, key: str) -> AgentSession | None:
        return self._sessions.get(key)

    def save(self, session: AgentSession) -> None:
        self._sessions[session.key] = session
