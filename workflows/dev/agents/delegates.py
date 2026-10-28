"""The agent delegates each stage runs, the profiles they run under, and their prompt arguments."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from loop import DEFAULT, AgentRequest, AgentWorkspace, Role

from workflows.shared.github.issues.spec import Ticket

from ..model.branches import feature_branch as feature_branch_of
from ..model.definition import Owner
from ..model.payloads import spec_payload, ticket_payload
from ..model.records import (
    AgentCommitSubject, TicketContext, TicketResult as DevResult, TicketWorkflowError as DevError,
    PlanContext, PlanResult, ChoreContext, ChoreResult, VerifyContext, VerifyResult,
    ReviewContext, ReviewResult,
)
from ..model.reply import DevResponse, PlanResponse, VerifyResponse, ChoreResponse, ReviewResponse
from .prompts import render_prompt


DEVELOPER = replace(DEFAULT, name=Role.DEVELOPER)
PLANNER = replace(DEFAULT, name=Owner.PLANNER)
VERIFIER = replace(DEFAULT, name=Owner.VERIFIER)
CHORE = replace(DEFAULT, name=Owner.CHORE)
REVIEWER = replace(DEFAULT, name=Owner.REVIEWER)


def prompt_args(
    ticket: Ticket,
    identifier: AgentCommitSubject,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    """Values for every placeholder in the dev prompt template."""
    return {
        "TICKET_JSON": json.dumps(ticket_payload(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "COMMIT_SUBJECT_PREFIX": identifier.to_subject(),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def implement_ticket(context: TicketContext, worktree: AgentWorkspace, *, template: str) -> DevResult:
    """Runs one fresh implementation agent and decodes its response for the selected Ticket."""
    args = prompt_args(
        context.ticket,
        context.identifier,
        context.initiative_commits,
        worktree.path,
        context.base_branch,
        context.feature_branch,
    )
    prompt = render_prompt(template, args)
    output = worktree.agent(DEVELOPER).run(AgentRequest(prompt)).output
    return DevResponse(context.identifier).decode(output)


def chore_args(context: ChoreContext, worktree: Path) -> dict[str, str]:
    """Values for every placeholder in the chore prompt template."""
    return {
        "PULL_REQUEST_URL": context.pull_request_url,
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": context.base_branch,
        "FEATURE_BRANCH": context.feature_branch,
        "DIFF_DIR": str(context.diff_dir),
        "BRANCH_COMMITS": "\n\n".join(context.branch_commits) or "No commits exist on the feature branch yet.",
    }


def chore_pull_request(context: ChoreContext, worktree: AgentWorkspace, *, template: str) -> ChoreResult:
    """Runs one fresh chore agent over the pull request's captured diff; its edits stay uncommitted in the worktree."""
    prompt = render_prompt(template, chore_args(context, worktree.path))
    output = worktree.agent(CHORE).run(AgentRequest(prompt, context.session_name)).output
    return ChoreResponse().decode(output)


def plan_args(context: PlanContext, checkout: Path) -> dict[str, str]:
    """Values for every placeholder in the planner prompt template."""
    spec = context.spec
    feature_branch = feature_branch_of(spec)
    if feature_branch is None:
        raise DevError(f"cannot resolve feature branch for Spec {spec.url}")
    return {
        "CHECKOUT_PATH": str(checkout),
        "SPEC_JSON": json.dumps(spec_payload(spec), indent=2),
        "TARGET_BRANCH": context.base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def plan_spec(context: PlanContext, workspace: AgentWorkspace, *, template: str) -> PlanResult:
    """Runs one read-only planner agent on the checkout and decodes the Tickets it proposes."""
    prompt = render_prompt(template, plan_args(context, context.source_root or workspace.path))
    output = workspace.agent(PLANNER).run(AgentRequest(prompt, context.session_name)).output
    return PlanResponse().decode(output)


def verify_args(context: VerifyContext, worktree: Path) -> dict[str, str]:
    """Values for every placeholder in the verifier prompt template."""
    return {
        "WORKTREE_PATH": str(worktree),
        "FEATURE_BRANCH": context.feature_branch,
        "TARGET_BRANCH": context.base_branch,
        "SPEC_JSON": context.spec_json,
        "TICKETS_JSON": context.tickets_json,
        "BRANCH_COMMITS": "\n\n".join(context.branch_commits) or "No commits exist on the feature branch yet.",
        "PULL_REQUEST_URL": context.pull_request_url,
        "VERIFICATION_SKILLS": "\n   ".join(f"- `{skill}`" for skill in context.skills),
        "EVIDENCE_DIR": str(context.evidence_dir),
    }


def verify_app(context: VerifyContext, worktree: AgentWorkspace, *, template: str) -> VerifyResult:
    """Runs one fresh verifier agent that drives the app and fixes code-caused failures, leaving the fixes uncommitted."""
    prompt = render_prompt(template, verify_args(context, worktree.path))
    output = worktree.agent(VERIFIER).run(AgentRequest(prompt, context.session_name)).output
    return VerifyResponse().decode(output)


def review_args(context: ReviewContext) -> dict[str, str]:
    """Values for every placeholder in the reviewer prompt template."""
    return {
        "SOURCE_ROOT": str(context.source_root),
        "PULL_REQUEST_URL": context.pull_request_url,
        "INITIATIVE": context.initiative,
        "HEAD_SHA": context.head_sha,
    }


def review_pull_request(context: ReviewContext, workspace: AgentWorkspace, *, template: str) -> ReviewResult:
    """Runs one read-only reviewer agent on the checkout; it posts its own findings through the `code-review` skill."""
    prompt = render_prompt(template, review_args(context))
    output = workspace.agent(REVIEWER).run(AgentRequest(prompt, context.session_name)).output
    return ReviewResponse().decode(output)
