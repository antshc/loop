"""Configured repositories and their agent delegates."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .agent import implement_ticket, load_prompt
from .records import AgentWorkflow, Repository, RepositoryId


_ROOT = Path(__file__).resolve().parents[2]


HARNESS: Repository = Repository(
    checkout=_ROOT,
    worktrees=_ROOT / "workspace" / f"{_ROOT.name}.worktrees",
    owner_name=RepositoryId("antshc", "loop"),
)


@dataclass(frozen=True)
class RepositoryWorkflow:
    """One allowed target repository and the agent steps used to deliver its Tickets."""

    repository: Repository
    run_agents: AgentWorkflow


def configured_repositories(harness: Repository) -> tuple[RepositoryWorkflow, ...]:
    """Pairs each allowed checkout with its delegate; defaults to implementing on the harness."""
    implement = partial(implement_ticket, template=load_prompt("implement"))
    return (RepositoryWorkflow(harness, implement),)
