"""Which repositories the implement_spec Workflow targets, and the agent delegates each one runs."""

from __future__ import annotations

from ..agents.delegates import chore_pull_request, implement_ticket, plan_spec, review_pull_request
from ..model.records import Repository
from ..model.repositories import RepositoryWorkflow, Targets


def configured_repositories(harness: Repository) -> Targets:
    """Configures all eShop-style phases on the harness by default."""
    return Targets((RepositoryWorkflow(
        harness, implement_ticket,
        run_chore=chore_pull_request, run_plan=plan_spec, run_review=review_pull_request,
    ),))
