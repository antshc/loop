"""The harness, the configured target repositories, and the ports every dev slice shares."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NewType, Protocol

from workflows.shared.github.identity import RepositoryId
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.issue import Comment
from workflows.shared.github.repo import GitHubRepo

from .records import (
    AgentWorkflow, ChoreWorkflow, PlanWorkflow, Repository, ReviewWorkflow, TicketWorkflowError, VerifyWorkflow,
)


_ROOT = Path(__file__).resolve().parents[3]


HARNESS: Repository = Repository(
    checkout=_ROOT,
    worktrees=_ROOT / "workspace" / f"{_ROOT.name}.worktrees",
    owner_name=RepositoryId("antshc", "loop"),
)


Harness = NewType("Harness", Repository)
"""The repository whose tracker holds the Specs and Tickets."""


@dataclass(frozen=True)
class RepositoryWorkflow:
    """One target and its developer, planner, verifier, cleanup, and reviewer delegates."""

    repository: Repository
    run_agents: AgentWorkflow
    run_chore: ChoreWorkflow | None = None
    run_plan: PlanWorkflow | None = None
    run_verify: VerifyWorkflow | None = None
    run_review: ReviewWorkflow | None = None


class Targets:
    """The configured repository workflows, keyed case-insensitively by `owner/repo`."""

    def __init__(self, workflows: Sequence[RepositoryWorkflow]) -> None:
        self.workflows = tuple(workflows)

    def by_slug(self) -> dict[str, RepositoryWorkflow]:
        """Every workflow by its folded slug; a slug configured twice raises."""
        targets: dict[str, RepositoryWorkflow] = {}
        for workflow in self.workflows:
            target = str(workflow.repository.owner_name).casefold()
            if target in targets:
                raise TicketWorkflowError(f"duplicate repository workflow: {target}")
            targets[target] = workflow
        return targets


GithubFactory = Callable[[Repository], GitHubRepo]
"""The GitHub client of one configured repository."""


class Tracker(Protocol):
    """Where Specs and Tickets live; each command takes effect on GitHub at once, and a retry skips what is already applied."""

    def specs(self, number: int | None = None) -> Iterable[SpecGraphSnapshot]: ...

    def comment(self, number: int, purpose: str, body: str) -> Comment: ...

    def add_label(self, number: int, label: str) -> None: ...

    def close(self, number: int, comment: str) -> None: ...
