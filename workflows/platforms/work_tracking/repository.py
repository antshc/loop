"""Repository paths and GitHub identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


class RepositoryId(NamedTuple):
    """GitHub owner and repository name, represented as an owner/repo slug."""

    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class Repository:
    """One repository's checkout, worktree directory, and GitHub identity."""

    checkout: Path
    worktrees: Path
    owner_name: RepositoryId
