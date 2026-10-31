"""Repository configuration shared by pull-request and specification workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryTarget:
    """A configured repository and optional factory activities."""

    slug: str
    checkout: Path
    worktrees: Path
    plan: bool = True
    chore: bool = True
    review: bool = True
