from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loop import AgentWorkspace
from workflows.platforms.git import Commit, GitService
from workflows.platforms.work_tracking import AgentCommitSubject, RepositoryId, Spec, Ticket, TicketsTracker

logger = logging.getLogger("workflow.tickets")


class TicketWorkflowError(Exception):
    """An expected failure in ticket delivery or its agent delegate."""


class ExecutionStoreError(TicketWorkflowError):
    """The execution log on disk is unreadable."""


@dataclass(frozen=True)
class TicketContext:
    """The selected Ticket and branch history supplied to one agent-workflow attempt."""

    ticket: Ticket
    identifier: AgentCommitSubject
    initiative_commits: tuple[str, ...]
    base_branch: str
    feature_branch: str


@dataclass(frozen=True)
class TicketResult:
    """A delegate's completed commit, summary and verification, or its failure reason."""

    identifier: str
    status: str
    commit: str | None = None
    summary: str | None = None
    verification: str | None = None
    reason: str | None = None


AgentWorkflow = Callable[[TicketContext, AgentWorkspace], TicketResult]
Clock = Callable[[], datetime]


class FileExecutionStore:
    """Daily JSON array of failure records at `<directory>/<workflow>-execution-log-<UTC date>.json`."""

    def __init__(self, directory: Path, *, workflow: str = "dev", clock: Clock | None = None) -> None:
        self._directory = Path(directory)
        self._workflow = workflow
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def failed_attempts(self, key: str) -> int:
        """Returns today's persisted failure count for a Ticket URL."""
        record = self._find(self._load(), key)
        return record["count"] if record else 0

    def record_failure(
        self,
        key: str,
        *,
        owner: str,
        repo: str,
        task_id: str,
        title: str,
        items: Sequence[int],
    ) -> None:
        """Increments today's failure count for the given Ticket."""
        records = self._load()
        record = self._find(records, key)
        updated = {
            "owner": owner,
            "repo": repo,
            "type": "spec",
            "task_id": task_id,
            "title": title,
            "task": key,
            "count": (record["count"] if record else 0) + 1,
            "last_run": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_items": list(items),
        }
        if record is None:
            records.append(updated)
        else:
            records[records.index(record)] = updated
        self._save(records)

    def reset(self, key: str) -> None:
        """Clears today's failures for a delivered Ticket."""
        self._save([record for record in self._load() if record["task"] != key])

    def _path(self) -> Path:
        date = self._clock().strftime("%Y-%m-%d")
        return self._directory / f"{self._workflow}-execution-log-{date}.json"

    def _load(self) -> list[dict]:
        path = self._path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exception:
            raise ExecutionStoreError(f"execution log is not valid JSON: {path}") from exception
        if not isinstance(data, list):
            raise ExecutionStoreError(f"execution log must be a JSON array: {path}")
        return data

    def _save(self, records: list[dict]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, separators=(",", ":")))

    @staticmethod
    def _find(records: list[dict], key: str) -> dict | None:
        return next((record for record in records if record["task"] == key), None)


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
) -> bool:
    """Delivers actionable Tickets in order, retrying failed attempts and stopping at the failure cap."""
    if max_failures < 1:
        raise TicketWorkflowError("max_failures must be at least one")
    for ticket in spec.tickets:
        if not ticket.actionable:
            continue
        while True:
            head_before = git.head(worktree.path)
            context = _ticket_context(spec, ticket, worktree.path, git)
            result = _attempt(context, worktree, run_agents, git, head_before)
            if isinstance(result, TicketResult):
                spec.close_ticket(
                    ticket.number, f"Delivered in {result.commit}.\n\n{result.summary}\n\n{result.verification}"
                )
                tracker.update_spec(spec)
                store.reset(ticket.url)
                break
            logger.warning("ticket %s failed: %s", ticket.url, result)
            git.restore(worktree.path, head_before.sha)
            if _record_failure(store, ticket_repository, ticket) >= max_failures:
                spec.escalate(ticket.number, result)
                tracker.update_spec(spec)
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
) -> TicketResult | str:
    try:
        result = run_agents(context, worktree)
    except subprocess.CalledProcessError:
        return "agent process did not exit successfully"
    except TicketWorkflowError as exception:
        return str(exception)
    violation = _result_violation(context.identifier, result)
    if violation is not None:
        return violation
    violation = _commit_violation(git, worktree.path, head_before, context.identifier, result)
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
