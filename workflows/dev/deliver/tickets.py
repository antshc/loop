"""Ticket delivery, retries, and validation of the agent's commit."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loop import AgentWorkspace, Kind, Phase, Phases, Role

from workflows.shared.git import Commit, GitClient
from workflows.shared.github.identity import RepositoryId
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.spec import Ticket

from ..model.branches import feature_branch as feature_branch_of
from ..model.handoff import close_ticket, escalate
from ..model.records import AgentCommitSubject, AgentWorkflow, TicketContext, TicketResult, TicketWorkflowError
from ..model.repositories import Tracker
from .journal import FileExecutionStore


logger = logging.getLogger("workflow.tickets")


@dataclass(frozen=True)
class TicketsDelivered:
    """The Spec as it stands after delivery, and whether every Ticket was delivered."""

    spec: SpecGraphSnapshot
    complete: bool


class TicketDeliverer:
    """Delivers a Spec's actionable Tickets in order on one worktree, one validated agent commit per Ticket."""

    def __init__(
        self,
        git: GitClient,
        tracker: Tracker,
        store: FileExecutionStore,
        phases: Phases,
        *,
        ticket_repository: RepositoryId,
        max_failures: int = 2,
    ) -> None:
        self._git = git
        self._tracker = tracker
        self._store = store
        self._phases = phases
        self._ticket_repository = ticket_repository
        self._max_failures = max_failures

    def deliver(
        self,
        spec: SpecGraphSnapshot,
        worktree: AgentWorkspace,
        run_agents: AgentWorkflow,
        publish: Callable[[SpecGraphSnapshot], object] = lambda spec: None,
    ) -> TicketsDelivered:
        """Retries failed attempts and stops at the failure cap; `publish` runs with the current Spec after each closed Ticket."""
        if self._max_failures < 1:
            raise TicketWorkflowError("max_failures must be at least one")
        for ticket in spec.deliverable_tickets:
            if not ticket.actionable:
                continue
            attempt = 0
            while True:
                attempt += 1
                head_before = self._git.head(worktree.path)
                context = self._context(spec, ticket, worktree.path)
                with self._phases.phase("ticket", Kind.AGENT, Role.DEVELOPER, ticket=ticket.number, attempt=attempt) as phase:
                    result = self._attempt(context, worktree, run_agents, head_before, phase)
                if isinstance(result, TicketResult):
                    with self._phases.phase("close-ticket", Kind.CODE, Role.CODE, ticket=ticket.number):
                        spec = close_ticket(
                            self._tracker,
                            spec,
                            ticket.number,
                            f"Delivered in {result.commit}.\n\n{result.summary}\n\n{result.verification}",
                        )
                        self._store.reset(ticket.url)
                    publish(spec)
                    break
                logger.debug("ticket %s failed: %s", ticket.url, result)
                with self._phases.phase("restore", Kind.CODE, Role.CODE, ticket=ticket.number):
                    self._git.restore(worktree.path, head_before.sha)
                if self._record_failure(ticket) >= self._max_failures:
                    with self._phases.phase("escalate", Kind.CODE, Role.CODE, ticket=ticket.number) as escalation:
                        spec = escalate(self._tracker, spec, ticket.number, result)
                        escalation.set(verdict="human input required")
                    return TicketsDelivered(spec, False)
        return TicketsDelivered(spec, True)

    def _context(self, spec: SpecGraphSnapshot, ticket: Ticket, path: Path) -> TicketContext:
        base_branch, feature_branch = spec.base_branch, feature_branch_of(spec)
        if base_branch is None or feature_branch is None:
            raise TicketWorkflowError(f"cannot resolve branches for Spec {spec.url}")
        identifier = AgentCommitSubject(spec.initiative, ticket.number)
        commits = self._git.initiative_commits(path, base_branch, identifier.initiative_commit_prefix)
        return TicketContext(
            ticket,
            identifier,
            tuple(f"{commit.sha[:7]} {commit.subject}" for commit in commits),
            base_branch,
            feature_branch,
        )

    def _attempt(
        self, context: TicketContext, worktree: AgentWorkspace, run_agents: AgentWorkflow, head_before: Commit, phase: Phase
    ) -> TicketResult | str:
        try:
            result = run_agents(context, worktree)
        except subprocess.CalledProcessError:
            phase.fail("Agent process failed")
            return "agent process did not exit successfully"
        except TicketWorkflowError as exception:
            phase.fail("Agent response was rejected")
            return str(exception)
        with self._phases.phase("validate", Kind.CODE, Role.CODE, ticket=context.ticket.number) as validation:
            violation = _result_violation(context.identifier, result)
            if violation is None:
                violation = self._commit_violation(worktree.path, head_before, context.identifier, result)
            validation.gate("Ticket result and commit are valid", violation is None, violation or "")
        if violation is not None:
            phase.fail("Ticket result or commit validation failed")
        return result if violation is None else violation

    def _commit_violation(
        self, path: Path, head_before: Commit, identifier: AgentCommitSubject, result: TicketResult
    ) -> str | None:
        head = self._git.head(path)
        if head.sha == head_before.sha:
            return "HEAD did not change: the agent made no commit"
        new_commits = self._git.commits_since(path, head_before.sha)
        if len(new_commits) != 1:
            return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
        prefix = identifier.to_subject()
        if not head.subject.startswith(prefix):
            return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
        if not self._git.is_clean(path):
            return "the worktree has uncommitted changes"
        if result.commit != head.sha:
            return f"result.commit {result.commit!r} does not equal HEAD {head.sha!r}"
        return None

    def _record_failure(self, ticket: Ticket) -> int:
        self._store.record_failure(
            ticket.url,
            owner=self._ticket_repository.owner,
            repo=self._ticket_repository.repo,
            task_id=str(ticket.number),
            title=ticket.title,
            items=[ticket.number],
        )
        return self._store.failed_attempts(ticket.url)


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
