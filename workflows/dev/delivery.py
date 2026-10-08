"""Delivers a Spec's Tickets on its worktree, one fresh agent run per attempt."""

from __future__ import annotations

from loop import AgentRunner, AgentRunnerProvider, Cancelled, Commit, LoopError
from workflows.platforms.work_tracking import Repository, Spec, Ticket, WorkIdentifier

from .acceptance import commit_violation
from .deps import DevDeps
from .prompting import initiative_commits, prompt_args
from .result import DevResult, DevResultError, parse_response
from .settings import MAX_TICKET_FAILURES


def open_runner(spec: Spec, repository: Repository, deps: DevDeps) -> AgentRunner:
    """Creates the feature-branch agent runner for `spec`."""
    provider = AgentRunnerProvider(
        deps.git, deps.harness_root, deps.agent_factory, executor=deps.executor, cancel=deps.cancel
    )
    return provider.create(
        checkout=repository.path,
        base=spec.base_branch,
        branch=spec.feature_branch,
        hooks=tuple(deps.hooks),
        worktree_root=repository.worktree_root,
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


def _record_failure(ticket: Ticket, deps: DevDeps) -> int:
    """Counts one failure for `ticket` and returns its persisted total."""
    owner, repo = deps.repository_pool.harness.owner_repo.split("/", 1)
    deps.store.record_failure(
        ticket.url,
        owner=owner,
        repo=repo,
        task_id=str(ticket.number),
        title=ticket.title,
        items=[ticket.number],
    )
    return deps.store.failed_attempts(ticket.url)


def _deliver_ticket(spec: Spec, ticket: Ticket, runner: AgentRunner, deps: DevDeps) -> bool:
    """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
    worktree = runner.worktree.path
    identifier = WorkIdentifier(spec.initiative, ticket.number)
    while True:
        head_before = deps.git.commits.head(worktree)
        args = prompt_args(
            ticket,
            identifier,
            initiative_commits(deps.git.commits, worktree, spec.base_branch, spec.initiative),
            worktree,
            spec.base_branch,
            spec.feature_branch,
        )
        attempt = _run_and_validate(runner, deps, identifier, head_before, args)
        if isinstance(attempt, DevResult):
            spec.close_ticket(
                ticket.number, f"Delivered in {attempt.commit}.\n\n{attempt.summary}\n\n{attempt.verification}"
            )
            deps.tracker.update_spec(spec)
            deps.store.reset(ticket.url)
            return True
        deps.git.commits.restore(head_before)
        if _record_failure(ticket, deps) >= MAX_TICKET_FAILURES:
            spec.escalate(ticket.number, attempt)
            deps.tracker.update_spec(spec)
            return False

def deliver_tickets(spec: Spec, runner: AgentRunner, deps: DevDeps) -> bool:
    """Deliver Ticket for each actionable Ticket in order; stops at the first failure."""
    for ticket in spec.tickets:
        if not _deliver_ticket(spec, ticket, runner, deps):
            return False
    return True
