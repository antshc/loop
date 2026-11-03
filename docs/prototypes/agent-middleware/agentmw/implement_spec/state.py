"""The state of one Spec's run: its route, the services Stages use, and what earlier Stages produced."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..host import AgentRun
from ..integrations import WorktreeGit
from .policy import SOURCE
from .tracker import Tracker


@dataclass(frozen=True)
class SpecRoute:
    """The Spec's routing: its Initiative, branches, read-only checkout and feature worktree."""

    spec: int
    initiative: str
    base_branch: str
    feature_branch: str
    checkout: str
    worktree: str

    def metadata(self, path: str, **extra: str) -> dict[str, str]:
        """The request metadata of a call that runs in `path`."""
        return {
            "spec": str(self.spec),
            "initiative": self.initiative,
            "base_branch": self.base_branch,
            "feature_branch": self.feature_branch,
            "worktree": path,
            **extra,
        }


@dataclass(frozen=True)
class Outcome:
    """How the Spec's run ended: `ready`, `draft`, `handed_off`, `failed`, `idle` or `held`."""

    status: str
    reason: str = ""
    pull_request: str | None = None


@dataclass(frozen=True)
class Delivery:
    """The delivered head and the commits on the feature branch."""

    head: str
    commits: tuple[str, ...]


@dataclass
class SpecRun:
    """The Workflow context: what Stages read, and the results and failures they leave."""

    route: SpecRoute
    agents: AgentRun
    tracker: Tracker
    git: WorktreeGit
    # The workspace agent calls run in; `within` switches it.
    path: str = ""
    item: Any = None
    results: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    escalated: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = self.path or self.route.checkout

    def metadata(self, **extra: str) -> dict[str, str]:
        """The request metadata of a call in the current workspace."""
        return self.route.metadata(self.path, **extra)

    async def comment(self, text: str) -> None:
        """Comments on the Spec, prefixed with the Workflow's name."""
        await self.tracker.comment(self.route.spec, f"{SOURCE}: {text}")
