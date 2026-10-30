"""The chore agent's request and replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...contract import Declined, Message


@dataclass(frozen=True)
class ChoreRequest(Message):
    """A published pull request and its captured per-file diff, refined in the worktree at `worktree`."""

    pull_request_url: str
    worktree: Path
    base_branch: str
    feature_branch: str
    diff_dir: Path
    branch_commits: tuple[str, ...] = ()


@dataclass(frozen=True)
class Refined(Message):
    """The proposed commit message and the findings the agent left alone; both may be empty."""

    commit_message: str = ""
    summary: str = ""
    out_of_scope: tuple[str, ...] = ()


type ChoreReply = Refined | Declined
