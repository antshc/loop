"""The Events of diagnosing a pull request's CI failure, rerunning it and fixing it."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from software_factory.agents import Json
from software_factory.runtime import Event


@dataclass(frozen=True)
class Transient(Event):
    """The failure is not the branch's doing and a rerun may clear it."""

    reason: str


@dataclass(frozen=True)
class BranchBroken(Event):
    """The branch itself breaks CI; `reason` is the evidence-backed cause."""

    reason: str


type Diagnosis = Transient | BranchBroken


@dataclass(frozen=True)
class CiRerun(Event):
    """The failed workflow runs `runs` of `head` were rerun while the CI-run signal was `signal`."""

    head: str
    signal: str
    runs: tuple[int, ...]


@dataclass(frozen=True)
class CiFixed(Event):
    """The maintainer fixed CI in the worktree on pull request head `head`, to be published as a batch without feedback items."""

    head: str
    reason: str
    items: tuple[dict[str, Json], ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        """No feedback item: a CI fix replies to nobody."""
        return ()

    @property
    def token(self) -> str:
        """Identifies the fix across runs by the head it was made on."""
        return hashlib.sha256(f"ci:{self.head}".encode()).hexdigest()[:16]
