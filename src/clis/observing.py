from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from ..observe import AgentRunRecord, LogReading, RunObserver
from ..run import AgentRequest, RunContext
from ..sessions import CliOutcome, Turn
from .base import AgentProfile
from .runner import CliRunner

logger = logging.getLogger(__name__)


class SessionLogReader(Protocol):
    """Adapter for one CLI's own session log; keyed by `cli`, the `AgentCli.name` it serves."""

    cli: str

    def start_args(self, turn: Turn) -> tuple[str, ...]:
        """Extra CLI arguments that make the log findable from `turn`; empty on a Resume."""
        ...

    def read(self, turn: Turn) -> LogReading | None:
        """This turn's usage and tools; None when unobservable. May raise OSError, ValueError, KeyError or TypeError."""
        ...


def _now() -> datetime:
    return datetime.now(UTC)


class ObservingCliRunner:
    """Decorates a CliRunner: after each turn it reports an AgentRunRecord to the observer and never changes the result."""

    def __init__(
        self,
        inner: CliRunner,
        observer: RunObserver,
        readers: Sequence[SessionLogReader] = (),
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._inner = inner
        self._observer = observer
        self._readers = {reader.cli: reader for reader in readers}
        self._clock = clock

    def run(
        self,
        profile: AgentProfile,
        request: AgentRequest,
        turn: Turn,
        context: RunContext,
    ) -> CliOutcome:
        reader = self._readers.get(profile.cli.name)
        started_at = self._clock()
        try:
            outcome = self._inner.run(
                self._pinned(profile, reader, turn), request, turn, context
            )
        except Exception as exception:
            self._report(
                profile,
                request,
                turn,
                context,
                started_at,
                reader,
                None,
                repr(exception),
            )
            raise
        self._report(profile, request, turn, context, started_at, reader, outcome, None)
        return outcome

    @staticmethod
    def _pinned(
        profile: AgentProfile, reader: SessionLogReader | None, turn: Turn
    ) -> AgentProfile:
        extra = reader.start_args(turn) if reader is not None else ()
        return replace(profile, args=(*profile.args, *extra)) if extra else profile

    def _report(
        self,
        profile: AgentProfile,
        request: AgentRequest,
        turn: Turn,
        context: RunContext,
        started_at: datetime,
        reader: SessionLogReader | None,
        outcome: CliOutcome | None,
        error: str | None,
    ) -> None:
        reading = self._read(reader, turn)
        record = AgentRunRecord(
            cli=profile.cli.name,
            session=turn.name.value,
            native_id=reading.native_id if reading else None,
            cwd=context.agent.cwd,
            started_at=started_at,
            ended_at=self._clock(),
            exit_code=outcome.exit_code if outcome else None,
            error=error,
            model=(reading.model if reading else None) or profile.model,
            reasoning_effort=(reading.reasoning_effort if reading else None)
            or profile.reasoning_effort,
            usage=reading.usage if reading else None,
            tools=reading.tools if reading else (),
            prompt=request.prompt,
            output=outcome.output if outcome else None,
            agent=profile.name,
        )
        try:
            self._observer.agent_run(record)
        except (
            Exception
        ) as exception:  # An observer is caller code; it must never fail the run.
            logger.warning("observer failed on agent run: %r", exception)

    @staticmethod
    def _read(reader: SessionLogReader | None, turn: Turn) -> LogReading | None:
        if reader is None:
            return None
        try:
            return reader.read(turn)
        except (OSError, ValueError, KeyError, TypeError) as exception:
            logger.warning("session log unreadable: %r", exception)
            return None
