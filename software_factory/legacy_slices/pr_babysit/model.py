"""The babysit domain model: the maintained PR, its durable state, and the outcomes that block it."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from software_factory.shared.state import JsonFile, StateError, process_lock


class Blocked(RuntimeError):
    """A PR needs intervention before its maintenance can continue."""


class BaselineFailed(Blocked):
    """Review edits must wait for the maintainer to repair the baseline."""


@dataclass(frozen=True)
class PrTarget:
    """The one PR a maintenance process owns."""

    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/pull/{self.number}"


@dataclass(frozen=True)
class DiscoveryRequest:
    """How the discovery scheduler was asked to run."""

    config: Path
    include_existing: bool = False
    retry: str | None = None


class Pacer(Protocol):
    """Waits between polls."""

    def wait(self, seconds: float) -> None: ...


class SleepPacer:
    """Waits by sleeping."""

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


class StopSignal:
    """Set by a signal handler; polled by the long-running loops."""

    def __init__(self) -> None:
        self._requested = False

    def request(self) -> None:
        self._requested = True

    def requested(self) -> bool:
        return self._requested


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Holds the cross-process lock at `path`; Blocked when another process owns it."""
    with ExitStack() as stack:
        try:
            stack.enter_context(process_lock(path))
        except StateError as exception:
            raise Blocked(str(exception)) from exception
        yield


def read_state(file: JsonFile, default: dict) -> dict:
    """Versioned JSON state; Blocked rather than reset when it is unreadable."""
    try:
        return file.read_versioned(default)
    except StateError as exception:
        raise Blocked(str(exception)) from exception


_STATUSES = {"queued", "ignored", "running", "blocked", "completed", "interrupted"}


class PrState:
    """One PR's pending work and publication checkpoints."""

    def __init__(self, root: Path, repo: str, number: int) -> None:
        self.path = root / "prs" / f"{repo.replace('/', '--')}--{number}.json"
        self._file = JsonFile(self.path)
        self.data = read_state(self._file, {
            "version": 1, "repo": repo, "number": number, "status": "queued",
            "items": {}, "retries": {}, "copilot": {}, "batch": None,
        })
        if self.data.get("repo") != repo or self.data.get("number") != number:
            raise Blocked(f"State identity mismatch: {self.path}")
        if (self.data.get("status") not in _STATUSES
                or not all(isinstance(self.data.get(key), dict) for key in ("items", "retries", "copilot"))
                or (self.data.get("batch") is not None and not isinstance(self.data["batch"], dict))
                or any(not isinstance(item, dict) or item.get("status") not in {"pending", "handled", "superseded"}
                       for item in self.data["items"].values())):
            raise Blocked(f"Invalid PR state: {self.path}")

    @classmethod
    def of(cls, root: Path, target: PrTarget) -> PrState:
        return cls(root, target.repo, target.number)

    def save(self) -> None:
        """Checkpoints the current transaction."""
        self._file.write(self.data)

    def status(self, status: str, **values) -> None:
        """Records the execution outcome and recovery information."""
        self.data.update(status=status, **values)
        self.save()
