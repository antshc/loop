"""Delivers a SpecRun's Tickets in its sandbox, one fresh agent run per attempt."""

from __future__ import annotations

from loop import AgentOptions, Cancelled, LoopError, SandboxHooks, Ticket, WorktreeSandbox, create_sandbox

from .acceptance import commit_violation
from .deps import DevDeps
from .planning import SpecRun
from .prompting import initiative_commits, prompt_args
from .result import DevResult, DevResultError, parse_response
from .settings import MAX_TICKET_FAILURES
from .tracker import close_delivered, escalate, record_ticket_failure


def open_sandbox(run: SpecRun, deps: DevDeps) -> WorktreeSandbox:
    """Creates the feature-branch worktree sandbox for `run`."""
    return create_sandbox(
        deps.git,
        deps.sandbox_factory,
        checkout=run.checkout,
        harness_root=deps.harness_root,
        base=run.base_branch,
        branch=run.feature_branch,
        hooks=SandboxHooks(worktree_ready=tuple(deps.hooks)),
        cancel=deps.cancel,
    )


def _run_and_validate(
    sandbox: WorktreeSandbox,
    deps: DevDeps,
    identifier: str,
    head_before: str,
    args: dict[str, str],
) -> DevResult | str:
    """Runs the agent and validates its response and Git; returns the accepted result, or the failure reason."""
    try:
        outcome = sandbox.run(deps.agent_factory, deps.template, args, AgentOptions(session_key=None)).result
    except Cancelled:
        raise
    except LoopError as exception:
        return str(exception)

    try:
        dev_result = parse_response(outcome.response, identifier)
    except DevResultError as exception:
        return str(exception)
    if dev_result.status == "failed":
        return dev_result.reason or ""
    if not outcome.success:
        return "agent process did not exit successfully"

    violation = commit_violation(deps.git, sandbox.worktree, head_before, identifier, dev_result)
    return dev_result if violation is None else violation


def _deliver_ticket(run: SpecRun, ticket: Ticket, sandbox: WorktreeSandbox, deps: DevDeps) -> bool:
    """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
    worktree = sandbox.worktree
    identifier = task_id(run.initiative, ticket.number)
    while True:
        head_before = deps.git.head(worktree)
        args = prompt_args(
            ticket,
            identifier,
            initiative_commits(deps.git, worktree, run.base_branch, run.initiative),
            worktree,
            run.base_branch,
            run.feature_branch,
        )
        attempt = _run_and_validate(sandbox, deps, identifier, head_before, args)
        if isinstance(attempt, DevResult):
            close_delivered(deps.harness_github, ticket.number, attempt)
            deps.store.reset(ticket.url)
            return True
        deps.git.reset_to(worktree, head_before)
        if record_ticket_failure(deps.store, deps.harness_slug, ticket) >= MAX_TICKET_FAILURES:
            escalate(deps.harness_github, run.spec.number, ticket.number, attempt)
            return False

def task_id(initiative: str, ticket_number: int) -> str:
    """The response envelope's `identifier`, and the commit subject's parenthesized tag."""
    return f"{initiative}|{ticket_number}"

def deliver_tickets(run: SpecRun, sandbox: WorktreeSandbox, deps: DevDeps) -> bool:
    """Deliver Ticket for each actionable Ticket in order; stops at the first failure."""
    for ticket in run.actionable:
        if not _deliver_ticket(run, ticket, sandbox, deps):
            return False
    return True
