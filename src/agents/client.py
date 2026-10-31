from __future__ import annotations

import logging
from dataclasses import replace
from typing import Protocol

from ..clis import AgentProfile, CliRunner
from ..run import AgentContext, AgentRequest, AgentResult, RunContext
from ..run.instructions import resolve_instructions
from ..run.launch import apply_start_mode
from ..sessions import (
    Resume,
    SessionCliMismatch,
    SessionName,
    SessionStore,
    Start,
    Turn,
)

logger = logging.getLogger(__name__)


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(
        self, request: AgentRequest, context: AgentContext | None = None
    ) -> AgentResult: ...

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
        workspace: bool = False,
        dry_run: bool = False,
    ) -> None:
        if session is not None and store is None:
            raise ValueError("session requires with_session()")
        profile.validate_permissions()
        self._instructions = resolve_instructions(profile.instructions, dry_run=dry_run)
        self._runner = runner
        self._profile = profile
        defaults = defaults or RunContext()
        self._defaults = replace(
            defaults, agent=profile.apply_context(defaults.agent, workspace=workspace)
        )
        self._store = store
        self._session = session or (SessionName.new() if store is not None else None)

    def run(
        self, request: AgentRequest, context: AgentContext | None = None
    ) -> AgentResult:
        if request.name is not None and self._store is not None:
            raise ValueError("request name requires a stateless client")
        run_context = (
            self._defaults
            if context is None
            else replace(self._defaults, agent=context)
        )
        if self._instructions is not None:
            request = replace(
                request,
                prompt=f"## Agent instructions\n{self._instructions}\n\n## Task\n{request.prompt}",
            )
        agent, request = apply_start_mode(run_context.agent, request)
        if agent is not run_context.agent:
            logger.info(
                "harness start: cwd=%s worktree=%s", agent.cwd, run_context.agent.cwd
            )
            run_context = replace(run_context, agent=agent)
        turn = self._turn()
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
                raise SessionCliMismatch(
                    f"handle for {handle.cli} found under {cli.name}"
                )
            logger.info("resuming session %s on %s", self._session.value, cli.name)
            logger.debug("native handle: %s", handle)
            return Resume(self._session, handle)
        new_handle = cli.handle_for_new(self._session)
        if new_handle is not None:
            # Saved before the run so a retry resumes instead of colliding on the name.
            self._store.save(self._session, new_handle)
        logger.info("starting session %s on %s", self._session.value, cli.name)
        return Start(self._session)

    def close(self) -> None:
        pass
