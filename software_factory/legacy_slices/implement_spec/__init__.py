"""Composition interface for the self-contained implement_spec Workflow."""

from __future__ import annotations

from software_factory.shared.github.identity import RepositoryId

from .agents.delegates import chore_pull_request, implement_ticket, plan_spec, review_pull_request
from .composition.entry import main
from .composition.providers import use_repositories
from .deliver.journal import ExecutionStoreError, FileExecutionStore
from .model.records import (
    AgentWorkflow,
    PlanContext, PlannedTicket, PlanResult, PlanWorkflow,
    ChoreContext, ChoreResult, ChoreWorkflow,
    ReviewContext, ReviewAxis, ReviewFinding, ReviewResult, ReviewWorkflow,
    Repository,
    TicketContext,
    TicketResult,
    TicketResult as DevResult,
    TicketWorkflowError,
)
from .model.repositories import HARNESS, RepositoryWorkflow

__all__ = [
    "HARNESS",
    "AgentWorkflow",
    "PlanContext", "PlannedTicket", "PlanResult", "PlanWorkflow",
    "ChoreContext", "ChoreResult", "ChoreWorkflow",
    "ReviewContext", "ReviewAxis", "ReviewFinding", "ReviewResult", "ReviewWorkflow",
    "plan_spec", "chore_pull_request", "review_pull_request",
    "DevResult",
    "ExecutionStoreError",
    "FileExecutionStore",
    "Repository",
    "RepositoryId",
    "RepositoryWorkflow",
    "TicketContext",
    "TicketResult",
    "TicketWorkflowError",
    "implement_ticket",
    "main",
    "use_repositories",
]
