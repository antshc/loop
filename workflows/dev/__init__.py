"""Composition interface for the self-contained dev Workflow."""

from __future__ import annotations

from .agents.delegates import (
    chore_pull_request, implement_ticket, plan_spec, prompt_args, review_pull_request, verify_app,
)
from .agents.prompts import PROMPT, PROMPTS, PromptError, load_prompt, render_prompt
from .composition.entry import main
from .composition.providers import use_repositories
from .deliver.journal import ExecutionStoreError, FileExecutionStore
from .model.records import (
    AgentWorkflow,
    PlanContext, PlannedTicket, PlanResult, PlanWorkflow,
    VerifyContext, VerifyFinding, VerifyResult, VerifyWorkflow,
    ChoreContext, ChoreResult, ChoreWorkflow,
    ReviewContext, ReviewAxis, ReviewFinding, ReviewResult, ReviewWorkflow,
    Repository,
    RepositoryId,
    TicketContext,
    TicketResult,
    TicketResult as DevResult,
    TicketWorkflowError,
)
from .model.reply import DevResponse, DevResultError, parse_dev_result
from .model.repositories import HARNESS, RepositoryWorkflow

__all__ = [
    "HARNESS",
    "PROMPT",
    "PROMPTS",
    "AgentWorkflow",
    "PlanContext", "PlannedTicket", "PlanResult", "PlanWorkflow",
    "VerifyContext", "VerifyFinding", "VerifyResult", "VerifyWorkflow",
    "ChoreContext", "ChoreResult", "ChoreWorkflow",
    "ReviewContext", "ReviewAxis", "ReviewFinding", "ReviewResult", "ReviewWorkflow",
    "plan_spec", "verify_app", "chore_pull_request", "review_pull_request",
    "DevResponse",
    "DevResult",
    "DevResultError",
    "ExecutionStoreError",
    "FileExecutionStore",
    "PromptError",
    "Repository",
    "RepositoryId",
    "RepositoryWorkflow",
    "TicketContext",
    "TicketResult",
    "TicketWorkflowError",
    "implement_ticket",
    "load_prompt",
    "main",
    "parse_dev_result",
    "prompt_args",
    "render_prompt",
    "use_repositories",
]
