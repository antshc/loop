"""The maintainer's requests and replies, one pair per mode."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Json, Message


@dataclass(frozen=True)
class DiagnoseRequest(Message):
    """A pull request snapshot and its failed CI logs to diagnose; the agent only reads."""

    snapshot: Json
    logs: str


@dataclass(frozen=True)
class Diagnosis(Message):
    """Whether the failure is `branch`-related, `transient` or `blocked`, with its evidence-backed cause."""

    classification: str
    reason: str = ""


@dataclass(frozen=True)
class FixRequest(Message):
    """A branch-related diagnosis to repair in the worktree."""

    diagnosis: Diagnosis
    snapshot: Json


@dataclass(frozen=True)
class Fixed(Message):
    """The outcome of a fix attempt: `fixed` or `blocked`, with what changed or why not."""

    verdict: str
    reason: str = ""
