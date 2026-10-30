"""The Execution ledger: one append-only, versioned JSONL log per work item, written only by the runtime."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from ..agents.contract import Json

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

    @classmethod
    def placed(cls, workflow: str, item: str, path: tuple[str, ...], stage_id: str, revision: str, epoch: int) -> WorkKey:
        """The key of the Stage `stage_id` placed in the scope at `path` of work item `item`, bound to `revision` in `epoch`."""
        return cls("/".join((workflow, item, *path, stage_id)), revision, epoch)

    def as_json(self) -> dict[str, Json]:
        """The stored form."""
        return {"item": self.item, "revision": self.revision, "epoch": self.epoch}


@dataclass(frozen=True)
class Attempt:
    """One started attempt: its number under its key, the commit it started from and the worktree status then."""

    n: int
    base: str | None
    status: str | None


@dataclass(frozen=True)
class AttemptState:
    """The newest attempt under a key, merged from its records; `final` says whether its outcome ends the key's rework."""

    n: int
    status: AttemptStatus
    base: str | None
    porcelain: str | None
    reply: Json
    tree: str | None
    message: str | None
    commit: str | None
    event: Json
    reason: str | None
    final: bool
    error: str | None

    @property
    def settled(self) -> bool:
        """Whether the attempt ended in an outcome a later run replays rather than redoes."""
        return self.status == "passed" or (self.status in ("failed", "held") and self.final)

    @property
    def interrupted(self) -> bool:
        """Whether the attempt started but never recorded an outcome."""
        return self.status in ("running", "executed", "committing")


@dataclass(frozen=True)
class RunStatus:
    """How a work item's latest run stands, read by an intake; `running` means a run started and has not closed."""

    epoch: int
    status: Literal["running", "completed", "idle", "held", "handed_off", "failed", "interrupted"]
    reason: str | None
    attributes: Mapping[str, Json]
    released: bool


@dataclass(frozen=True)
class OpenScope:
    """A scope a run started and never closed: the cursor a later run resumes from."""

    path: tuple[str, ...]
    item: Json
    revision: str
    head: str | None
    last_commit: str | None = None

    @property
    def last_head(self) -> str | None:
        """The commit a resumed scope must still be at: its last accepted commit, else the head it opened at."""
        return self.last_commit or self.head


@dataclass
class RunOpening:
    """What a run starts from: its epoch, whether it resumes an unfinished or held epoch, and that epoch's open scopes and recorded checks."""

    epoch: int
    resumed: bool
    scopes: dict[tuple[str, ...], OpenScope]
    pure: dict[tuple[tuple[str, ...], str], list[dict[str, Any]]]


class WorkItemLocked(Exception):
    """Another process holds the work item."""


class LedgerCorrupted(ValueError):
    """A work item's log holds a damaged line other than a torn last one; `path` names the log."""

    def __init__(self, path: Path, line: int, detail: str) -> None:
        super().__init__(f"damaged ledger line {line} in {path}: {detail}")
        self.path = path


class Ledger:
    """Root of every work item's log: `<root>/<workflow>/<slug>-<sha256(item)[:12]>/log.jsonl` and `lock`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def item(self, workflow: str, item: str) -> WorkItemLog:
        """The log of one work item of one Workflow."""
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", item).strip("-")[:40] or "item"
        digest = hashlib.sha256(item.encode()).hexdigest()[:12]
        return WorkItemLog(self._root / workflow / f"{slug}-{digest}")

    def release(self, workflow: str, item: str, reason: str) -> None:
        """Releases held work: the next run closes its handed-off scopes and starts a fresh epoch; written by intake under the item's lock."""
        log = self.item(workflow, item)
        with log.lock(wait=True):
            log.append({"kind": "released", "reason": reason})

    def status(self, workflow: str, item: str) -> RunStatus | None:
        """The status and attributes of the work item's latest run, or None when it never ran."""
        return self.item(workflow, item).status()


class WorkItemLog:
    """Append-only; each record is one fsynced JSON line, and a torn last line is ignored as if never written."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self.path = directory / "log.jsonl"
        self._records: list[dict[str, Any]] | None = None
        self._intact = 0
        self._scopes: list[ScopeLog] = []

    @contextmanager
    def lock(self, *, wait: bool = False) -> Iterator[None]:
        """Holds the item's lock; raises WorkItemLocked when another process holds it and `wait` is not set."""
        self._directory.mkdir(parents=True, exist_ok=True)
        with (self._directory / "lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError:
                raise WorkItemLocked(str(self._directory)) from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def records(self) -> tuple[dict[str, Any], ...]:
        """Every record written so far, in order."""
        return tuple(self._loaded())

    def last_epoch(self) -> int:
        """The newest epoch the log holds; 0 when it has none."""
        return self._scan().epoch

    def status(self) -> RunStatus | None:
        """How the latest run stands; None when no run ever started."""
        scan = self._scan()
        if scan.epoch == 0:
            return None
        if scan.unfinished or scan.closed is None:
            return RunStatus(scan.epoch, "running", None, {}, scan.released)
        closed = scan.closed
        return RunStatus(scan.epoch, closed["status"], closed.get("reason"), dict(closed.get("attributes", {})), scan.released)

    def open_epoch(self) -> RunOpening:
        """Opens this run's epoch: an unfinished or held-and-unreleased epoch is resumed, a released one is closed, and any other finished run is followed by the next epoch."""
        scan = self._scan()
        held = scan.closed is not None and scan.closed["status"] == "handed_off"
        resumed = scan.unfinished or (held and not scan.released)
        if resumed:
            opening = RunOpening(scan.epoch, True, scan.scopes, scan.pure)
        else:
            for path in reversed(list(scan.scopes) if held else []):
                self.append({"kind": "scope-closed", "path": list(path), "status": "released"})
            opening = RunOpening(scan.epoch + 1, False, {}, {})
        self.append({"kind": "run-opened", "epoch": opening.epoch})
        return opening

    def close_run(self, status: str, reason: str | None, attributes: Mapping[str, Json]) -> None:
        """Records how the run ended."""
        self.append({"kind": "run-closed", "status": status, "reason": reason, "attributes": dict(attributes)})

    def scope(
        self, parent: ScopeLog | None, path: tuple[str, ...], item: Any, revision: str, head: Callable[[], str | None],
        resumed: OpenScope | None = None,
    ) -> ScopeLog:
        """A scope that writes nothing until its first durable record; a resumed one is already open."""
        scope = ScopeLog(self, parent, path, item, revision, head)
        scope.opened = resumed is not None
        self._scopes.append(scope)
        return scope

    def forget(self, scope: ScopeLog) -> None:
        """Stops tracking a closed scope, so a long run holds only the scopes still open."""
        if scope in self._scopes:
            self._scopes.remove(scope)

    def has_replay(self, key: WorkKey) -> bool:
        """Whether a once-per-key replay is already recorded under `key`, or waits to be."""
        stored = [*self._loaded(), *(record for scope in self._scopes for record in scope.pending)]
        return any(record["kind"] == "replayed" and record["key"] == key.as_json() for record in stored)

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
        self.seed([record])

    def seed(self, records: Sequence[Mapping[str, Any]]) -> None:
        """Writes `records` as durable lines in one write, so a crash leaves a torn tail the next read drops rather than a partial history; for adopting state the factory did not produce."""
        stored = [{"v": LEDGER_VERSION, **record} for record in records]
        self._loaded()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._drop_torn_tail()
        lines = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in stored)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(lines)
            handle.flush()
            os.fsync(handle.fileno())
        self._intact += len(lines.encode())
        self._loaded().extend(stored)

    def _scan(self) -> _Scan:
        scan = _Scan()
        for record in self._loaded():
            scan.take(record)
        return scan

    def _loaded(self) -> list[dict[str, Any]]:
        if self._records is None:
            self._records = self._read()
        return self._records

    def _drop_torn_tail(self) -> None:
        """Cuts a torn last line off the file so the next record starts on a line of its own."""
        if self.path.exists() and self.path.stat().st_size > self._intact:
            os.truncate(self.path, self._intact)

    def _read(self) -> list[dict[str, Any]]:
        self._intact = 0
        if not self.path.exists():
            return []
        *lines, _unterminated = self.path.read_bytes().split(b"\n")
        records: list[dict[str, Any]] = []
        for number, line in enumerate(lines):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exception:
                # A torn last line never happened; damage anywhere else is real.
                if number != len(lines) - 1:
                    raise LedgerCorrupted(self.path, number + 1, str(exception)) from exception
                break
            self._intact += len(line) + 1
        return records


@dataclass
class _Scan:
    """What a pass over the records yields: the run's standing and the scopes and checks of its latest epoch."""

    epoch: int = 0
    unfinished: bool = False
    closed: dict[str, Any] | None = None
    released: bool = False
    scopes: dict[tuple[str, ...], OpenScope] = field(default_factory=dict)
    pure: dict[tuple[tuple[str, ...], str], list[dict[str, Any]]] = field(default_factory=dict)

    def take(self, record: dict[str, Any]) -> None:
        """Folds one record in."""
        match record["kind"]:
            case "run-opened":
                if record["epoch"] != self.epoch:
                    self.epoch = record["epoch"]
                    self.scopes, self.pure = {}, {}
                self.unfinished, self.closed = True, None
            case "run-closed":
                self.unfinished, self.closed, self.released = False, record, False
            case "released":
                self.released = True
            case "scope-opened":
                path = tuple(record["path"])
                self.scopes[path] = OpenScope(path, record["item"], record["revision"], record["head"])
            case "scope-closed":
                self.scopes.pop(tuple(record["path"]), None)
            case "pure":
                self.pure.setdefault((tuple(record["path"]), record["stage"]), []).append(record)
            case "attempt" if record["status"] == "passed" and record.get("commit"):
                self._advance(tuple(record["path"]), record["commit"])

    def _advance(self, path: tuple[str, ...], commit: str) -> None:
        for scope_path, scope in self.scopes.items():
            if path[: len(scope_path)] == scope_path:
                self.scopes[scope_path] = replace(scope, last_commit=commit)


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
        self.path = path
        self._item = item
        self._revision = revision
        self._head = head
        self.opened = False
        self.pending: list[dict[str, Any]] = []

    def pure(
        self, stage: str, revision: str, outcome: str, event: Event | None, reason: str | None, error: str | None = None,
    ) -> None:
        """Buffers a check Stage's outcome, bound to the revision it saw; it is written with the scope's next durable record, never alone."""
        record = {
            "kind": "pure", "path": list(self.path), "stage": stage, "revision": revision, "outcome": outcome,
            "event": encode(event), "reason": reason, "error": error,
        }
        self.pending.append(record)

    def ensure(self) -> None:
        """Writes what is owed before a durable record: the enclosing scopes' openings, this opening, buffered outcomes."""
        if self._parent is not None:
            self._parent.ensure()
        if not self.opened:
            self.opened = True
            self._log.append({
                "kind": "scope-opened", "path": list(self.path), "item": encode(self._item),
                "revision": self._revision, "head": self._head(),
            })
        self._flush()

    def replayed(self, key: WorkKey, event: Event | None) -> None:
        """Records, once per epoch, that a once-per-key Stage replayed an earlier pass here; in a scope not yet opened it waits for the scope's first durable record, so a quiet poll writes nothing."""
        if self._log.has_replay(key):
            return
        record = {"kind": "replayed", "key": key.as_json(), "event": encode(event)}
        if not self.opened:
            self.pending.append(record)
            return
        self.ensure()
        self._log.append(record)

    def loop(self, key: WorkKey, budget: Mapping[str, str] | None = None) -> AttemptLoop:
        """The attempts under one work key; `budget` names the once-per-key or at-most-N rule they count against across runs."""
        return AttemptLoop(self._log, self, key, budget)

    def close(self, status: str) -> None:
        """Records the scope's end if it ever wrote anything; a handed-off or interrupted scope stays open. A scope that completed without writing is forgotten with its buffered outcomes, which nothing will ever write."""
        closure = {"completed": "completed", "idle": "completed", "failed": "failed", "held": "held"}.get(status)
        if closure is None:
            return
        if not self.opened:
            if closure == "completed":
                self.pending.clear()
                self._log.forget(self)
            return
        self._flush()
        self._log.append({"kind": "scope-closed", "path": list(self.path), "status": closure})
        self._log.forget(self)

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
        return sum(1 for record in self._mine() if record["status"] == "running")

    def last(self) -> AttemptState | None:
        """The newest attempt under this key, or None when none was recorded."""
        mine = self._mine()
        if not mine:
            return None
        n = max(record["n"] for record in mine)
        merged: dict[str, Any] = {}
        for record in mine:
            if record["n"] == n:
                merged.update(record)
        return AttemptState(
            n=n, status=merged["status"], base=merged.get("base"), porcelain=merged.get("porcelain"),
            reply=merged.get("reply"), tree=merged.get("tree"), message=merged.get("message"), commit=merged.get("commit"),
            event=merged.get("event"), reason=merged.get("reason"), final=merged.get("final", True), error=merged.get("error"),
        )

    def begin(self, base: str | None, status: str | None) -> Attempt:
        """Records the attempt's intent; call before the attempt has any external effect."""
        self._scope.ensure()
        attempt = Attempt(self._next_n(), base, status)
        self._record(attempt, "running", porcelain=status)
        return attempt

    def executed(self, attempt: Attempt, reply: Any, tree: str | None = None) -> None:
        """Records the executor's reply and the worktree content it left."""
        self._record(attempt, "executed", reply=encode(reply), tree=tree)

    def committing(self, attempt: Attempt, message: str, event: Event) -> None:
        """Records the commit about to be made and the Event it carries."""
        self._record(attempt, "committing", message=message, event=encode(event))

    def finish(
        self, attempt: Attempt, status: Literal["passed", "failed", "held"], *, reason: str | None = None,
        commit: str | None = None, event: Event | None = None, final: bool = True, error: str | None = None,
    ) -> None:
        """Records the attempt's outcome; `final` is false for a failure the Stage's rework will follow with another attempt."""
        self._record(attempt, status, reason=reason, commit=commit, event=encode(event), final=final, error=error)

    def refuse(self, reason: str) -> None:
        """Records a final failure for a placement that was refused before any attempt started, so a later run replays the refusal."""
        self._scope.ensure()
        self._record(Attempt(self._next_n(), None, None), "failed", reason=reason, final=True)

    def _next_n(self) -> int:
        return max((record["n"] for record in self._mine()), default=0) + 1

    def _record(self, attempt: Attempt, status: AttemptStatus, **fields: Any) -> None:
        budget = {} if self._budget is None else {"budget": self._budget}
        self._log.append({
            "kind": "attempt", "key": self._key.as_json(), "path": list(self._scope.path), "n": attempt.n,
            "status": status, "base": attempt.base, **budget, **fields,
        })

    def _mine(self) -> list[dict[str, Any]]:
        return [record for record in self._log.records() if record["kind"] == "attempt" and record["key"] == self._key.as_json()]
