"""Which repositories the implement_spec Workflow targets, and the agent delegates each one runs."""

from __future__ import annotations

from functools import partial

from ..agents.delegates import chore_pull_request, implement_ticket, plan_spec, review_pull_request
from ..agents.prompts import load_prompt
from ..model.records import Repository
from ..model.repositories import RepositoryWorkflow, Targets


def configured_repositories(harness: Repository) -> Targets:
    """Configures all eShop-style phases on the harness by default."""
    implement = partial(implement_ticket, template=load_prompt("implement"))
    return Targets((RepositoryWorkflow(
        harness, implement,
        run_chore=partial(chore_pull_request, template=load_prompt("chore")),
        run_plan=partial(plan_spec, template=load_prompt("plan")),
        run_review=partial(review_pull_request, template=load_prompt("review")),
    ),))
