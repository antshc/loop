"""The developer's request and replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loop import Declined, Json, Message


@dataclass(frozen=True)
class DeveloperRequest(Message):
    """One Ticket to implement in `worktree`; the agent must echo `identifier` and commit with `commit_subject_prefix`."""

    identifier: str
    ticket: Json
    initiative_commits: tuple[str, ...]
    commit_subject_prefix: str
    worktree: Path
    base_branch: str
    feature_branch: str


@dataclass(frozen=True)
class Delivered(Message):
    """The agent committed the Ticket."""

    commit: str
    summary: str
    verification: str


type DeveloperReply = Delivered | Declined
