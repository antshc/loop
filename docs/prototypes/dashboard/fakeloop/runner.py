"""The phase primitive: a context manager that records a phase's start, end, and failure."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .model import EventRecord, Phase, PhaseParams, new_id, now_iso
from .tracer import Tracer


class PhaseHandle:
    """What the body of a `with run.phase(...)` block works with."""

    def __init__(self, run: Run, phase: Phase) -> None:
        self.run = run
        self.phase = phase

    def log(self, **payload: object) -> None:
        """Records a `log` event; an engineer phase's `input` also becomes the session request."""
        self.run.tracer.event(EventRecord(
            adw_id=self.run.adw_id, phase_id=self.phase.phase_id, type="log",
            name=self.phase.params.name, payload=dict(payload)))
        if self.phase.params.kind == "engineer" and "input" in payload:
            self.run.tracer.session_request(self.run.adw_id, str(payload["input"]))


class Run:
    """One session: an id, a tracer, a session directory, and the phases entered so far."""

    def __init__(self, tracer: Tracer, data_dir: Path, engineer: str, adw_name: str,
                 speed: float = 1.0) -> None:
        self.tracer = tracer
        self.adw_id = new_id(12)
        self.speed = speed
        self.phases: list[Phase] = []
        self.session_dir = data_dir / "sessions" / self.adw_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        tracer.session_start(self.adw_id, engineer, adw_name)

    def sleep(self, seconds: float) -> None:
        """Pauses so the dashboard can be watched live; `--speed 0` skips the wait."""
        time.sleep(seconds * self.speed)

    def add_usage(self, tokens: int, cost: float) -> None:
        """Adds to the session's totals."""
        self.tracer.session_add_usage(self.adw_id, tokens, cost)

    @contextmanager
    def phase(self, params: PhaseParams) -> Iterator[PhaseHandle]:
        """Runs one phase; success is earned by a clean exit, any exception marks it failed."""
        self._seq += 1
        phase = Phase(phase_id=f"{self.adw_id}_{self._seq:02d}_{params.name}",
                      adw_id=self.adw_id, seq=self._seq, params=params,
                      started_at=now_iso())
        self.phases.append(phase)
        self.tracer.phase_upsert(phase)
        self._emit(phase, "phase_start", {"kind": params.kind, "owner": params.owner,
                                          "description": params.description})
        try:
            yield PhaseHandle(self, phase)
        except BaseException as exception:
            phase.status = "fail"
            phase.error = str(exception)[:1000]
            phase.ended_at = now_iso()
            self._emit(phase, "error", {"error": phase.error})
            self._emit(phase, "phase_end", {"status": "fail"})
            self.tracer.phase_upsert(phase)
            self.tracer.session_finish(self.adw_id, ok=False)
            raise
        phase.status = "success"
        phase.ended_at = now_iso()
        self._emit(phase, "phase_end", {"status": "success"})
        self.tracer.phase_upsert(phase)

    def finish(self) -> int:
        """Settles the session from its phases and returns the exit code."""
        ok = bool(self.phases) and all(phase.status == "success" for phase in self.phases)
        self.tracer.session_finish(self.adw_id, ok=ok)
        return 0 if ok else 1

    def _emit(self, phase: Phase, event_type: str, payload: dict[str, object]) -> None:
        self.tracer.event(EventRecord(adw_id=self.adw_id, phase_id=phase.phase_id,
                                      type=event_type, name=phase.params.name,
                                      payload=payload))
