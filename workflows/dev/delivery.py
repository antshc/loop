"""Delivers a SpecRun's Tickets on its worktree, one fresh agent run per attempt."""

from __future__ import annotations

from loop import AgentRunner, AgentRunnerProvider, Cancelled, Commit, LoopError
from workflows.platforms.work_tracking import Ticket, WorkIdentifier

from .acceptance import commit_violation
from .deps import DevDeps
from .planning import SpecRun
from .prompting import initiative_commits, prompt_args
from .result import DevResult, DevResultError, parse_response
from .settings import MAX_TICKET_FAILURES


def open_runner(run: SpecRun, deps: DevDeps) -> AgentRunner:
    """Creates the feature-branch agent runner for `run`."""
    provider = AgentRunnerProvider(
        deps.git, deps.harness_root, deps.agent_factory, executor=deps.executor, cancel=deps.cancel
    )
    return provider.create(
        checkout=run.repository.path,
        base=run.spec.base_branch,
        branch=run.spec.feature_branch,
        hooks=tuple(deps.hooks),
        worktree_root=run.repository.worktree_root,
    )


def _run_and_validate(
    runner: AgentRunner,
    deps: DevDeps,
    identifier: WorkIdentifier,
    head_before: Commit,
    args: dict[str, str],
) -> DevResult | str:
    """Runs the agent and validates its response and Git; returns the accepted result, or the failure reason."""
    try:
        outcome = runner.run(deps.template, args).result
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

    violation = commit_violation(deps.git.worktrees, deps.git.commits, runner.worktree, head_before, identifier, dev_result)
    return dev_result if violation is None else violation


def _deliver_ticket(run: SpecRun, ticket: Ticket, runner: AgentRunner, deps: DevDeps) -> bool:
    """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
    worktree = runner.worktree.path
    identifier = WorkIdentifier(run.spec.initiative, ticket.number)
    while True:
        head_before = deps.git.commits.head(worktree)
        args = prompt_args(
            ticket,
            identifier,
            initiative_commits(deps.git.commits, worktree, run.spec.base_branch, run.spec.initiative),
            worktree,
            run.spec.base_branch,
            run.spec.feature_branch,
        )
        attempt = _run_and_validate(runner, deps, identifier, head_before, args)
        if isinstance(attempt, DevResult):
            deps.tracker.close_delivered(
                ticket, f"Delivered in {attempt.commit}.\n\n{attempt.summary}\n\n{attempt.verification}"
            )
            return True
        deps.git.commits.restore(head_before)
        if deps.tracker.record_failure(ticket) >= MAX_TICKET_FAILURES:
            deps.tracker.escalate(run.spec.number, ticket.number, attempt)
            return False

def deliver_tickets(run: SpecRun, runner: AgentRunner, deps: DevDeps) -> bool:
    """Deliver Ticket for each actionable Ticket in order; stops at the first failure."""
    for ticket in run.tickets:
        if not _deliver_ticket(run, ticket, runner, deps):
            return False
    return True
