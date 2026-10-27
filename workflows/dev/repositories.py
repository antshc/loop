"""Configured repositories and their agent delegates."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .agent import implement_ticket, load_prompt, plan_spec, verify_app, chore_pull_request, review_pull_request
from .records import AgentWorkflow, PlanWorkflow, VerifyWorkflow, ChoreWorkflow, ReviewWorkflow, Repository, RepositoryId


_ROOT = Path(__file__).resolve().parents[2]


HARNESS: Repository = Repository(
    checkout=_ROOT,
    worktrees=_ROOT / "workspace" / f"{_ROOT.name}.worktrees",
    owner_name=RepositoryId("antshc", "loop"),
)


@dataclass(frozen=True)
class RepositoryWorkflow:
    """One target and its developer, planner, verifier, cleanup, and reviewer delegates."""

    repository: Repository
    run_agents: AgentWorkflow
    run_chore: ChoreWorkflow | None = None
    run_plan: PlanWorkflow | None = None
    run_verify: VerifyWorkflow | None = None
    run_review: ReviewWorkflow | None = None


def configured_repositories(harness: Repository) -> tuple[RepositoryWorkflow, ...]:
    """Configures all eShop-style phases on the harness by default."""
    implement = partial(implement_ticket, template=load_prompt("implement"))
    return (RepositoryWorkflow(
        harness, implement,
        run_chore=partial(chore_pull_request, template=load_prompt("chore")),
        run_plan=partial(plan_spec, template=load_prompt("plan")),
        run_verify=partial(verify_app, template=load_prompt("verify")),
        run_review=partial(review_pull_request, template=load_prompt("review")),
    ),)
