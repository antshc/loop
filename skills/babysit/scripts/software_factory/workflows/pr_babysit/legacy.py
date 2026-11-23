"""One-time adoption of the earlier version's per-pull-request state (version 1) into the Execution ledger, so no in-flight work is lost; the state file is left where it is."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loop import Event, Ledger, Nothing, WorkItemLocked, WorkKey, encode

from software_factory.src.pull_requests.events.ci import (
    BranchBroken,
    CiFixed,
    CiRerun,
    Transient,
)
from software_factory.src.pull_requests.events.feedback import FeedbackHandled
from software_factory.src.pull_requests.events.pull_requests import (
    CiFailing,
    CopilotRequested,
    PullRequestWork,
)
from software_factory.src.pull_requests.stages.pacing import Poll

from .definition import NAME
from .policy import ADOPTED, FINGERPRINT, REASON

__all__ = ["adopt_v1", "read_status", "state_path"]

_EPOCH = 1
_ADOPTED_STAGES = ("adopted",)

# The scope paths below name the Flows and openers of the definition: the poll loop, the maintain-ci case and the worktree.
_POLL = ("poll", "poll-1")
_POLL_REVISION = "poll-1"
_MAINTAIN = (*_POLL, "maintain-ci", "CiFailing")


def state_path(state_dir: Path, slug: str, number: int) -> Path:
    """Where the earlier version kept the state of one pull request."""
    return state_dir / "prs" / f"{slug.replace('/', '--')}--{number}.json"


def read_status(path: Path) -> str | None:
    """The status the earlier version recorded in the state file at `path`, or None when there is no readable state."""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("status"))
    except OSError, ValueError, AttributeError:
        return None


def adopt_v1(
    state_file: Path,
    ledger: Ledger,
    work: PullRequestWork,
    *,
    ci_signal: Callable[[], str],
) -> bool:
    """Seeds the pull request's ledger from the earlier version's state, once and under the work item's lock; False when there is no state, the ledger already holds records or another process holds the lock. `ci_signal` reads the CI signal of the pull request now, which an open CI batch needs to not be diagnosed again."""
    if not state_file.exists():
        return False
    # The state file stays after adoption, so this runs on every scan: never wait on a lock a live run holds.
    if ledger.item(NAME, work.id).records():
        return False
    # A fresh handle under the lock: the one above cached what it read before another process could write.
    log = ledger.item(NAME, work.id)
    try:
        with log.lock():
            if log.records():
                return False
            log.seed(
                _Adoption(
                    _read(state_file, work), work, state_file, ci_signal
                ).records()
            )
    except WorkItemLocked:
        return False
    return True


def _read(path: Path, work: PullRequestWork) -> dict[str, Any]:
    """The state, checked to be the version-1 state of this pull request."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exception:
        raise ValueError(f"Cannot read state {path}: {exception}") from exception
    valid = (
        isinstance(state, dict)
        and state.get("version") == 1
        and state.get("repo") == work.target.slug
        and state.get("number") == work.number
        and isinstance(state.get("status"), str)
        and all(
            isinstance(state.get(key), dict) for key in ("items", "retries", "copilot")
        )
        and (state.get("batch") is None or isinstance(state["batch"], dict))
    )
    if not valid:
        raise ValueError(f"Invalid PR state: {path}")
    return state


@dataclass
class _Adoption:
    """The ledger records that stand for one version-1 state: an epoch whose open scopes are the batch and whose Events and attempts are what the state remembered."""

    state: dict[str, Any]
    work: PullRequestWork
    source: Path
    ci_signal: Callable[[], str]
    out: list[dict[str, Any]] = field(default_factory=list)

    def records(self) -> list[dict[str, Any]]:
        """Every record, in the order a run would have written them."""
        self.out.append({"kind": "run-opened", "epoch": _EPOCH})
        batch = self.state.get("batch")
        # An open address batch is not resumed: the address gate decides afresh what the discussion still needs.
        ci_batch = bool(batch) and batch.get("kind") == "ci"
        if ci_batch:
            self._ci_batch(batch)
        self._handled()
        self._copilot()
        self._reruns()
        self._close(ci_batch)
        self.out.append({"kind": "adopted", "source": str(self.source), "version": 1})
        return self.out

    # ---- the open batch ----------------------------------------------------------------------------------

    def _ci_batch(self, batch: dict[str, Any]) -> None:
        """The CI batch as an open fix scope: a committed fix is a passed attempt awaiting publication, an interrupted commit a committing one."""
        base, head, index = batch["base"], batch["head"], batch["index"]
        worktree = (*_MAINTAIN, "pr-worktree")
        fix = (*worktree, "fix", "BranchBroken")
        fixed = CiFixed(base, "adopted from the per-PR state")
        self._scope(_POLL, Poll(1), _POLL_REVISION, None)
        self._scope(_MAINTAIN, CiFailing(base), _POLL_REVISION, None)
        self._scope(worktree, None, _POLL_REVISION, head)
        self._scope(fix, BranchBroken(fixed.reason), _POLL_REVISION, head)
        self._pure(worktree, "require-clean-ci", Nothing())
        signal = self.ci_signal()
        self._adopted("diagnose-ci", signal, BranchBroken(fixed.reason), budget=signal)
        inflight = batch.get("inflight")
        if inflight:
            self._attempt(
                fix, "fix-ci", _POLL_REVISION, "running", parent=inflight["parent"]
            )
            self._attempt(
                fix,
                "fix-ci",
                _POLL_REVISION,
                "committing",
                parent=inflight["parent"],
                message=inflight["message"],
                event=fixed,
            )
        elif index:
            self._attempt(
                fix,
                "fix-ci",
                _POLL_REVISION,
                "passed",
                parent=base,
                commit=head,
                event=fixed,
            )

    # ---- what the state remembered -----------------------------------------------------------------------

    def _handled(self) -> None:
        """Feedback whose reply was posted, or that a newer comment overtook, is not selected again."""
        keys = tuple(
            key
            for key, item in self.state["items"].items()
            if item.get("status") in ("handled", "superseded")
        )
        if keys:
            self._adopted(
                "publish-batch",
                "handled",
                FeedbackHandled(keys, str(self.state.get("head", ""))),
            )

    def _copilot(self) -> None:
        """A Copilot review asked for on a head is not asked for again."""
        for head, outcome in self.state["copilot"].items():
            self._adopted(
                "request-copilot-review",
                head,
                CopilotRequested(head, outcome),
                budget=head,
            )

    def _reruns(self) -> None:
        """Each rerun spent on a head counts against its budget, and the failure the last one answered is not rerun or diagnosed again."""
        for head, used in self.state["retries"].items():
            for spent in range(1, used + 1):
                self._adopted(
                    "rerun-failed",
                    f"{head}:{spent}",
                    None,
                    budget=head,
                    status="running",
                )
        for head, wait in self.state.get("retry_wait", {}).items():
            signal = f"{head}:{wait}"
            self._adopted(
                "rerun-failed", f"{head}:wait", CiRerun(head, signal, ()), budget=head
            )
            self._adopted(
                "diagnose-ci",
                signal,
                Transient("adopted from the per-PR state"),
                budget=signal,
            )

    def _close(self, open_batch: bool) -> None:
        """A blocked pull request stays blocked at the fingerprint it was; one with an open batch otherwise resumes it."""
        status, reason = self.state["status"], self.state.get("reason") or None
        if status == "blocked":
            attributes = {
                FINGERPRINT: str(self.state.get("fingerprint", "")),
                REASON: reason or "",
                ADOPTED: True,
            }
            self.out.append(
                {
                    "kind": "run-closed",
                    "status": "handed_off",
                    "reason": reason,
                    "attributes": attributes,
                }
            )
        elif status == "completed":
            self.out.append(
                {
                    "kind": "run-closed",
                    "status": "completed",
                    "reason": None,
                    "attributes": {},
                }
            )
        elif not open_batch:
            self.out.append(
                {
                    "kind": "run-closed",
                    "status": "interrupted",
                    "reason": None,
                    "attributes": {},
                }
            )

    # ---- records -----------------------------------------------------------------------------------------

    def _scope(
        self, path: tuple[str, ...], item: Any, revision: str, head: str | None
    ) -> None:
        self.out.append(
            {
                "kind": "scope-opened",
                "path": list(path),
                "item": encode(item),
                "revision": revision,
                "head": head,
            }
        )

    def _pure(self, path: tuple[str, ...], stage: str, event: Event) -> None:
        self.out.append(
            {
                "kind": "pure",
                "path": list(path),
                "stage": stage,
                "revision": _POLL_REVISION,
                "outcome": "pass",
                "event": encode(event),
                "reason": None,
                "error": None,
            }
        )

    def _attempt(
        self,
        path: tuple[str, ...],
        stage: str,
        revision: str,
        status: str,
        *,
        parent: str | None = None,
        budget: dict[str, str] | None = None,
        **fields: Any,
    ) -> None:
        key = WorkKey.placed(NAME, self.work.id, path, stage, revision, _EPOCH)
        record = {
            "kind": "attempt",
            "key": key.as_json(),
            "path": list(path),
            "n": 1,
            "status": status,
            "base": parent,
        }
        if budget is not None:
            record["budget"] = budget
        self.out.append(
            {
                **record,
                **{
                    name: encode(value) if name == "event" else value
                    for name, value in fields.items()
                },
            }
        )

    def _adopted(
        self,
        stage: str,
        revision: str,
        event: Event | None,
        *,
        budget: str | None = None,
        status: str = "passed",
    ) -> None:
        """An attempt the earlier version made outside any scope, kept for its Event or its budget."""
        counted = None if budget is None else {"stage": stage, "per": budget}
        self._attempt(
            _ADOPTED_STAGES,
            stage,
            revision,
            status,
            budget=counted,
            **({} if event is None else {"event": event}),
        )
