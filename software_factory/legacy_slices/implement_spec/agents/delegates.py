"""The agent delegates each stage runs: each builds a role agent's request from the stage context and maps its reply back."""

from __future__ import annotations

from loop import AgentWorkspace

from software_factory.agents import AgentError, AgentReplyError, AgentRunError, Declined
from software_factory.agents.roles.chore import CHORE, ChoreRequest
from software_factory.agents.roles.developer import DEVELOPER, DeveloperRequest
from software_factory.agents.roles.planner import PLANNER, PlannerRequest
from software_factory.agents.roles.reviewer import REVIEWER, ReviewerRequest

from ..model.branches import feature_branch as feature_branch_of
from ..model.payloads import spec_payload, ticket_payload
from ..model.records import (
    AgentProcessError, TicketContext, TicketResult as DevResult, TicketWorkflowError as DevError,
    PlanContext, PlannedTicket, PlanResult, ChoreContext, ChoreResult,
    ReviewAxis, ReviewContext, ReviewFinding, ReviewResult,
)


def _failure(exception: AgentError) -> DevError:
    """The stage-level error for an agent failure; a run error keeps its process-failure meaning."""
    return AgentProcessError(str(exception)) if isinstance(exception, AgentRunError) else DevError(str(exception))


def implement_ticket(context: TicketContext, worktree: AgentWorkspace) -> DevResult:
    """Runs one fresh implementation agent and maps its reply for the selected Ticket."""
    identifier = str(context.identifier)
    request = DeveloperRequest(
        identifier, ticket_payload(context.ticket), context.initiative_commits, context.identifier.to_subject(),
        worktree.path, context.base_branch, context.feature_branch,
    )
    try:
        reply = DEVELOPER.run(worktree, request)
    except AgentError as exception:
        raise _failure(exception) from exception
    if isinstance(reply, Declined):
        return DevResult(identifier, "failed", reason=reply.reason)
    return DevResult(identifier, "completed", commit=reply.commit, summary=reply.summary, verification=reply.verification)


def chore_pull_request(context: ChoreContext, worktree: AgentWorkspace) -> ChoreResult:
    """Runs one fresh chore agent over the pull request's captured diff; its edits stay uncommitted in the worktree."""
    request = ChoreRequest(
        context.pull_request_url, worktree.path, context.base_branch, context.feature_branch, context.diff_dir, context.branch_commits,
    )
    try:
        reply = CHORE.run(worktree, request, session=context.session_name)
    except AgentReplyError:
        # The step is best effort, so an unreadable reply is an empty result.
        return ChoreResult()
    except AgentError as exception:
        raise _failure(exception) from exception
    if isinstance(reply, Declined):
        return ChoreResult()
    return ChoreResult(reply.commit_message, reply.summary, reply.out_of_scope)


def plan_spec(context: PlanContext, workspace: AgentWorkspace) -> PlanResult:
    """Runs one read-only planner agent on the checkout and maps the Tickets it proposes."""
    spec = context.spec
    feature_branch = feature_branch_of(spec)
    if feature_branch is None:
        raise DevError(f"cannot resolve feature branch for Spec {spec.url}")
    request = PlannerRequest(context.source_root or workspace.path, spec_payload(spec), context.base_branch, feature_branch)
    try:
        reply = PLANNER.run(workspace, request, session=context.session_name)
    except AgentError as exception:
        raise _failure(exception) from exception
    if isinstance(reply, Declined):
        return PlanResult("failed", reason=reply.reason)
    return PlanResult("completed", tuple(PlannedTicket(ticket.title, ticket.body, ticket.blocked_by) for ticket in reply.tickets))


def review_pull_request(context: ReviewContext, workspace: AgentWorkspace) -> ReviewResult:
    """Runs one read-only reviewer agent on the checkout; it posts its own findings through the `code-review` skill."""
    request = ReviewerRequest(context.pull_request_url, context.source_root, context.initiative, context.head_sha)
    try:
        reply = REVIEWER.run(workspace, request, session=context.session_name)
    except AgentError as exception:
        raise _failure(exception) from exception
    if isinstance(reply, Declined):
        raise DevError(f"review failed: {reply.reason}")
    return ReviewResult(
        tuple(ReviewAxis(axis.name, axis.outcome, axis.note) for axis in reply.axes),
        tuple(ReviewFinding(finding.kind, finding.summary, finding.axis) for finding in reply.findings),
        reply.posted,
        reply.head,
    )
