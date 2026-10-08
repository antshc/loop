"""Delivers a Spec's Tickets on its worktree, one fresh agent run per attempt."""

from __future__ import annotations

from loop import (
    AgentRunner,
    Cancelled,
    Commit,
    CommitService,
    LoopError,
    Worktree,
    WorktreeService,
)
from workflows.platforms.work_tracking import Spec, Ticket, WorkIdentifier

from .deps import DevDeps
from .prompting import initiative_commits, prompt_args
from .result import DevResult, DevResultError, parse_response
from .settings import MAX_TICKET_FAILURES


def commit_violation(
    worktrees: WorktreeService,
    commits: CommitService,
    worktree: Worktree,
    head_before: Commit,
    identifier: WorkIdentifier,
    dev_result: DevResult,
) -> str | None:
    """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
    head = commits.head(worktree.path)
    if head.sha == head_before.sha:
        return "HEAD did not change: the agent made no commit"
    new_commits = commits.since(head_before)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
    prefix = identifier.to_subject()
    if not head.subject.startswith(prefix):
        return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
    if not worktrees.is_clean(worktree):
        return "the worktree has uncommitted changes"
    if dev_result.commit != head.sha:
        return f"result.commit {dev_result.commit!r} does not equal HEAD {head.sha!r}"
    return None


def _run_and_validate(
    runner: AgentRunner,
    deps: DevDeps,
    identifier: WorkIdentifier,
    head_before: Commit,
    args: dict[str, str],
) -> DevResult | str:
    """Runs the agent and validates its response and Git; returns the accepted result, or the failure reason."""
    try:
        outcome = runner.run(deps.prompts.dev, args).result
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
