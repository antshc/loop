"""How each post-delivery step ended; produced by one slice and read by the next."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .records import ReviewResult


@dataclass(frozen=True)
class VerifyReport:
    """The settled outcome of app verification; `verdict` is a verdict or `skipped` when the repository has no verification skill."""

    verdict: str
    summary: str = ""
    evidence_dir: Path | None = None
    unfixed: tuple[str, ...] = ()
    fix_commit: str = ""

    @property
    def blocks_ready(self) -> bool:
        """Any verdict but `pass` or `skipped` keeps the PR a draft."""
        return self.verdict not in ("pass", "skipped")


SKIPPED = VerifyReport("skipped", "no app-verification skill found")


@dataclass(frozen=True)
class ChoreReport:
    """How the chore step ended: `committed` (with `commit`), `nothing` to change, or `discarded` (with the violation as `note`)."""

    status: str
    note: str = ""
    out_of_scope: tuple[str, ...] = ()
    commit: str = ""


@dataclass(frozen=True)
class Delivery:
    """What the worktree phase produced: the published pull request at its final head, and how each step ended."""

    pull_request_url: str
    head_sha: str
    delivered: bool
    commits: tuple[str, ...] = ()
    verification: VerifyReport = SKIPPED
    chore: ChoreReport | None = None


@dataclass(frozen=True)
class ReviewOutcome:
    result: ReviewResult | None = None
    error: str = ""
