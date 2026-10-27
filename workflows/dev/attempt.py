"""Ticket delivery, retries, and validation of the agent's commit."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from loop import AgentWorkspace, Kind, Phase, Phases, Role

from .checkout import GitService
from .journal import FileExecutionStore
from .platforms import TicketsTracker
from .records import (
    AgentCommitSubject, AgentWorkflow, Commit, RepositoryId, Spec, Ticket,
    TicketContext, TicketResult, TicketWorkflowError,
)


logger = logging.getLogger("workflow.tickets")


def deliver_tickets(
    spec: Spec,
    worktree: AgentWorkspace,
    run_agents: AgentWorkflow,
    *,
    git: GitService,
    tracker: TicketsTracker,
    store: FileExecutionStore,
    ticket_repository: RepositoryId,
    max_failures: int = 2,
    phases: Phases | None = None,
    publish: Callable[[], object] = lambda: None,
) -> bool:
    """Delivers actionable Tickets in order, retrying failed attempts and stopping at the failure cap."""
    if max_failures < 1:
        raise TicketWorkflowError("max_failures must be at least one")
    phases = phases or Phases()
    for ticket in spec.tickets:
        if not ticket.actionable:
            continue
        attempt = 0
        while True:
            attempt += 1
            head_before = git.head(worktree.path)
            context = _ticket_context(spec, ticket, worktree.path, git)
            with phases.phase("ticket", Kind.AGENT, Role.DEVELOPER, ticket=ticket.number, attempt=attempt) as phase:
                result = _attempt(context, worktree, run_agents, git, head_before, phases, phase)
            if isinstance(result, TicketResult):
                with phases.phase("close-ticket", Kind.CODE, Role.CODE, ticket=ticket.number):
                    spec.close_ticket(
                        ticket.number, f"Delivered in {result.commit}.\n\n{result.summary}\n\n{result.verification}"
                    )
                    tracker.update_spec(spec)
                    store.reset(ticket.url)
                publish()
                break
            logger.debug("ticket %s failed: %s", ticket.url, result)
            with phases.phase("restore", Kind.CODE, Role.CODE, ticket=ticket.number):
                git.restore(worktree.path, head_before.sha)
            if _record_failure(store, ticket_repository, ticket) >= max_failures:
                with phases.phase("escalate", Kind.CODE, Role.CODE, ticket=ticket.number) as escalation:
                    spec.escalate(ticket.number, result)
                    tracker.update_spec(spec)
                    escalation.set(verdict="human input required")
                return False
    return True


def _ticket_context(spec: Spec, ticket: Ticket, path: Path, git: GitService) -> TicketContext:
    base_branch, feature_branch = spec.base_branch, spec.feature_branch
    if base_branch is None or feature_branch is None:
        raise TicketWorkflowError(f"cannot resolve branches for Spec {spec.url}")
    identifier = AgentCommitSubject(spec.initiative, ticket.number)
    commits = git.initiative_commits(path, base_branch, identifier.initiative_commit_prefix)
    return TicketContext(
        ticket,
        identifier,
        tuple(f"{commit.sha[:7]} {commit.subject}" for commit in commits),
        base_branch,
        feature_branch,
    )


def _attempt(
    context: TicketContext,
    worktree: AgentWorkspace,
    run_agents: AgentWorkflow,
    git: GitService,
    head_before: Commit,
    phases: Phases,
    phase: Phase,
) -> TicketResult | str:
    ticket = context.ticket
    try:
        result = run_agents(context, worktree)
    except subprocess.CalledProcessError:
        phase.fail("Agent process failed")
        return "agent process did not exit successfully"
    except TicketWorkflowError as exception:
        phase.fail("Agent response was rejected")
        return str(exception)
    with phases.phase("validate", Kind.CODE, Role.CODE, ticket=ticket.number) as validation:
        violation = _result_violation(context.identifier, result)
        if violation is None:
            violation = _commit_violation(git, worktree.path, head_before, context.identifier, result)
        validation.gate("Ticket result and commit are valid", violation is None, violation or "")
    if violation is not None:
        phase.fail("Ticket result or commit validation failed")
    return result if violation is None else violation


def _result_violation(identifier: AgentCommitSubject, result: TicketResult) -> str | None:
    if result.identifier != str(identifier):
        return f"response identifier {result.identifier!r} does not match task {str(identifier)!r}"
    if result.status == "failed":
        return result.reason if isinstance(result.reason, str) else "failed result is missing reason"
    if result.status != "completed":
        return f"unknown result status: {result.status!r}"
    if not all(isinstance(value, str) for value in (result.commit, result.summary, result.verification)):
        return "completed result is missing a required commit, summary, or verification field"
    return None


def _commit_violation(
    git: GitService, path: Path, head_before: Commit, identifier: AgentCommitSubject, result: TicketResult
) -> str | None:
    head = git.head(path)
    if head.sha == head_before.sha:
        return "HEAD did not change: the agent made no commit"
    new_commits = git.commits_since(path, head_before.sha)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
    prefix = identifier.to_subject()
    if not head.subject.startswith(prefix):
        return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
    if not git.is_clean(path):
        return "the worktree has uncommitted changes"
    if result.commit != head.sha:
        return f"result.commit {result.commit!r} does not equal HEAD {head.sha!r}"
    return None


def _record_failure(store: FileExecutionStore, repository: RepositoryId, ticket: Ticket) -> int:
    store.record_failure(
        ticket.url,
        owner=repository.owner,
        repo=repository.repo,
        task_id=str(ticket.number),
        title=ticket.title,
        items=[ticket.number],
    )
    return store.failed_attempts(ticket.url)
