"""The Execution ledger: one append-only, versioned JSONL log per work item, written only by the runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from software_factory.agents import Json

from ._engine.codec import decode, encode, is_tagged
from .event import Event

LEDGER_VERSION = 1

type AttemptStatus = Literal["running", "executed", "committing", "passed", "failed", "held"]
type ScopeClosure = Literal["completed", "failed", "held", "released"]

_EVENT_STATUSES = ("passed", "failed", "held")


@dataclass(frozen=True)
class WorkKey:
    """What an attempt's evidence is bound to: the stage placement, the input revision and the run epoch."""

    item: str
    revision: str
    epoch: int

    def as_json(self) -> dict[str, Json]:
        """The stored form."""
        return {"item": self.item, "revision": self.revision, "epoch": self.epoch}


@dataclass(frozen=True)
class Attempt:
    """One started attempt: its number under its key, the commit it started from and the worktree status then."""

    n: int
    base: str | None
    status: str | None


class Ledger:
    """Root of every work item's log: `<root>/<workflow>/<slug>-<sha256(item)[:12]>/log.jsonl`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def item(self, workflow: str, item: str) -> WorkItemLog:
        """The log of one work item of one Workflow."""
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", item).strip("-")[:40] or "item"
        digest = hashlib.sha256(item.encode()).hexdigest()[:12]
        return WorkItemLog(self._root / workflow / f"{slug}-{digest}")


class WorkItemLog:
    """Append-only; each record is one fsynced JSON line."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "log.jsonl"
        self._records: list[dict[str, Any]] | None = None
        self._scopes: list[ScopeLog] = []

    def records(self) -> tuple[dict[str, Any], ...]:
        """Every record written so far, in order."""
        return tuple(self._loaded())

    def open_epoch(self) -> int:
        """The epoch of this run: the next one after a finished run, the same one after an unfinished run."""
        epoch = 0
        finished = True
        for record in self._loaded():
            if record["kind"] == "run-opened":
                epoch, finished = record["epoch"], False
            elif record["kind"] == "run-closed":
                finished = True
        epoch = epoch + 1 if finished else epoch
        self.append({"kind": "run-opened", "epoch": epoch})
        return epoch

    def close_run(self, status: str, reason: str | None, attributes: Mapping[str, Json]) -> None:
        """Records how the run ended."""
        self.append({"kind": "run-closed", "status": status, "reason": reason, "attributes": dict(attributes)})

    def scope(
        self, parent: ScopeLog | None, path: tuple[str, ...], item: Any, revision: str, head: Callable[[], str | None],
    ) -> ScopeLog:
        """A scope that writes nothing until its first durable record."""
        scope = ScopeLog(self, parent, path, item, revision, head)
        self._scopes.append(scope)
        return scope

    def root_scope(self, revision: str) -> ScopeLog:
        """The run's own scope; the run records already stand for its opening."""
        scope = self.scope(None, (), None, revision, lambda: None)
        scope.opened = True
        return scope

    def started_under(self, budget: Mapping[str, str]) -> int:
        """How many attempts, across every run, started against the budget; an interrupted attempt counts."""
        return sum(1 for record in self._loaded() if _counts_against(record, budget) and record["status"] == "running")

    def passed_under(self, budget: Mapping[str, str]) -> Mapping[str, Any] | None:
        """The latest attempt, across every run, that passed against the budget."""
        return next((record for record in reversed(self._loaded()) if _counts_against(record, budget) and record["status"] == "passed"), None)

    def history[T: Event](self, type_: type[T]) -> tuple[T, ...]:
        """Every `type_` event this work item recorded, across runs, in order; includes events not yet written."""
        stored = [*self._loaded(), *(record for scope in self._scopes for record in scope.pending)]
        events = (_event_of(record) for record in stored)
        return tuple(decode(event, type_) for event in events if is_tagged(event, type_))

    def append(self, record: Mapping[str, Any]) -> None:
        """Writes one record as a durable line."""
        stored = {"v": LEDGER_VERSION, **record}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stored, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._loaded().append(stored)

    def _loaded(self) -> list[dict[str, Any]]:
        if self._records is None:
            self._records = self._read()
        return self._records

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for number, line in enumerate(lines):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn last line never happened; damage anywhere else is real.
                if number != len(lines) - 1:
                    raise
        return records


def _counts_against(record: Mapping[str, Any], budget: Mapping[str, str]) -> bool:
    return record["kind"] == "attempt" and record.get("budget") == dict(budget)


def _event_of(record: Mapping[str, Any]) -> Json:
    """The event a record carries when it is a final outcome."""
    if record["kind"] == "attempt" and record["status"] in _EVENT_STATUSES:
        return record.get("event")
    if record["kind"] == "pure":
        return record.get("event")
    return None


class ScopeLog:
    """One scope's durable footprint, written lazily: its opening, its buffered check outcomes, then its attempts."""

    def __init__(
        self, log: WorkItemLog, parent: ScopeLog | None, path: tuple[str, ...], item: Any, revision: str,
        head: Callable[[], str | None],
    ) -> None:
        self._log = log
        self._parent = parent
        self._path = path
        self._item = item
        self._revision = revision
        self._head = head
        self.opened = False
        self.pending: list[dict[str, Any]] = []

    def pure(self, stage: str, outcome: str, event: Event | None, reason: str | None) -> None:
        """Buffers a check Stage's outcome; it is written with the scope's next durable record, never alone."""
        record = {"kind": "pure", "path": list(self._path), "stage": stage, "outcome": outcome, "event": encode(event), "reason": reason}
        self.pending.append(record)

    def ensure(self) -> None:
        """Writes what is owed before a durable record: the enclosing scopes' openings, this opening, buffered outcomes."""
        if self._parent is not None:
            self._parent.ensure()
        if not self.opened:
            self.opened = True
            self._log.append({
                "kind": "scope-opened", "path": list(self._path), "item": encode(self._item),
                "revision": self._revision, "head": self._head(),
            })
        self._flush()

    def loop(self, key: WorkKey, budget: Mapping[str, str] | None = None) -> AttemptLoop:
        """The attempts under one work key; `budget` names the once-per-key or at-most-N rule they count against across runs."""
        return AttemptLoop(self._log, self, key, budget)

    def close(self, status: str) -> None:
        """Records the scope's end if it ever wrote anything; a handed-off or interrupted scope stays open."""
        closure = {"completed": "completed", "idle": "completed", "failed": "failed", "held": "held"}.get(status)
        if not self.opened or closure is None:
            return
        self._flush()
        self._log.append({"kind": "scope-closed", "path": list(self._path), "status": closure})

    def _flush(self) -> None:
        pending, self.pending = self.pending, []
        for record in pending:
            self._log.append(record)


class AttemptLoop:
    """The attempts of one Stage placement under one work key; every state change is a new appended record."""

    def __init__(self, log: WorkItemLog, scope: ScopeLog, key: WorkKey, budget: Mapping[str, str] | None = None) -> None:
        self._log = log
        self._scope = scope
        self._key = key
        self._budget = None if budget is None else dict(budget)

    def started(self) -> int:
        """How many attempts under this key have started."""
        return sum(1 for record in self._log.records() if self._is_start(record))

    def begin(self, base: str | None, status: str | None) -> Attempt:
        """Records the attempt's intent; call before the attempt has any external effect."""
        self._scope.ensure()
        attempt = Attempt(self.started() + 1, base, status)
        self._record(attempt, "running")
        return attempt

    def executed(self, attempt: Attempt, reply: Any, tree: str | None = None) -> None:
        """Records the executor's reply and the worktree content it left."""
        self._record(attempt, "executed", reply=encode(reply), tree=tree)

    def committing(self, attempt: Attempt, message: str, event: Event) -> None:
        """Records the commit about to be made and the Event it carries."""
        self._record(attempt, "committing", message=message, event=encode(event))

    def finish(
        self, attempt: Attempt, status: Literal["passed", "failed", "held"], *, reason: str | None = None,
        commit: str | None = None, event: Event | None = None,
    ) -> None:
        """Records the attempt's outcome."""
        self._record(attempt, status, reason=reason, commit=commit, event=encode(event))

    def _record(self, attempt: Attempt, status: AttemptStatus, **fields: Any) -> None:
        budget = {} if self._budget is None else {"budget": self._budget}
        self._log.append({
            "kind": "attempt", "key": self._key.as_json(), "n": attempt.n, "status": status, "base": attempt.base, **budget, **fields,
        })

    def _is_start(self, record: Mapping[str, Any]) -> bool:
        return record["kind"] == "attempt" and record["status"] == "running" and record["key"] == self._key.as_json()
