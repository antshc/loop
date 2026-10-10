from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ..clis import AgentProfile, CliRunner
from ..run import AgentContext, AgentRequest, AgentResult, RunContext
from ..sessions import Resume, SessionCliMismatch, SessionName, SessionStore, Start, Turn


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(self, request: AgentRequest, context: AgentContext | None = None) -> AgentResult: ...

    def close(self) -> None: ...


class CliAgentClient:
    """Core agent bound to one CLI profile; decides start or resume, then delegates to its runner."""

    def __init__(
        self,
        runner: CliRunner,
        profile: AgentProfile,
        defaults: RunContext | None = None,
        *,
        store: SessionStore | None = None,
        session: SessionName | None = None,
    ) -> None:
        if session is not None and store is None:
            raise ValueError("session requires with_session()")
        self._runner = runner
        self._profile = profile
        self._defaults = defaults or RunContext()
        self._store = store
        self._session = session or (SessionName.new() if store is not None else None)

    def run(self, request: AgentRequest, context: AgentContext | None = None) -> AgentResult:
        turn = self._turn()
        run_context = self._defaults if context is None else replace(self._defaults, agent=context)
        outcome = self._runner.run(self._profile, request, turn, run_context)
        if self._store is not None:
            self._store.save(turn.name, outcome.handle)
        return AgentResult(outcome.output, turn.name, outcome.exit_code)

    def _turn(self) -> Turn:
        if self._store is None or self._session is None:
            return Start(SessionName.new())
        cli = self._profile.cli
        handle = self._store.get(self._session, cli.name)
        if handle is not None:
            if handle.cli != cli.name:
                raise SessionCliMismatch(f"handle for {handle.cli} found under {cli.name}")
            return Resume(self._session, handle)
        new_handle = cli.handle_for_new(self._session)
        if new_handle is not None:
            # Saved before the run so a retry resumes instead of colliding on the name.
            self._store.save(self._session, new_handle)
        return Start(self._session)

    def close(self) -> None:
        pass
