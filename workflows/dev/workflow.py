"""Spec orchestration: prepare, deliver, publish, and announce each open Spec."""

from __future__ import annotations

import logging
from enum import Enum, auto
from pathlib import Path

from loop import (
    AgentResult,
    AgentRunner,
    AgentRunnerProvider,
    Branch,
    Cancelled,
    Commit,
    LoopError,
    Prompt,
    RepositoryData,
    Worktree,
)
from workflows.platforms.work_tracking import Repository, Spec, Ticket, WorkIdentifier

from .deps import DevDeps
from .prompting import initiative_commits, prompt_args
from .result import DevResult, DevResultError, parse_response
from .settings import MAX_TICKET_FAILURES

logger = logging.getLogger("workflow.dev")


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


class DevWorkflow:
    """Delivers every open Spec through the dependencies it is constructed with."""

    def __init__(self, deps: DevDeps) -> None:
        self._tracker = deps.tracker
        self._repository_pool = deps.repository_pool
        self._git = deps.git
        self._store = deps.store
        self._hooks = tuple(deps.hooks)
        self._prompts = deps.prompts
        self._runner_provider = AgentRunnerProvider(
            deps.git, deps.harness_root, deps.agent_factory, executor=deps.executor, cancel=deps.cancel
        )

    def process_specs(self) -> int:
        """Processes every Spec not waiting for a human; returns the process exit code."""
        failed = False
        for spec in self._tracker.specs():
            if spec.awaiting_human:
                continue
            failed = (self._process_spec(spec) is Outcome.FAILED) or failed
        return 1 if failed else 0

    def _process_spec(self, spec: Spec) -> Outcome:
        """Prepare, deliver, and publish one Spec."""
        if spec.target is None or spec.base_branch is None:
            spec.block(f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
            self._tracker.update_spec(spec)
            return Outcome.SKIPPED

        repository = self._repository_pool.get(spec.target)
        if repository is None:
            spec.block(f"dev: repo:target:{spec.target} is not configured in the RepositoryPool")
            self._tracker.update_spec(spec)
            return Outcome.SKIPPED

        try:
            if not self._prepare(spec, repository):
                return Outcome.SKIPPED
            agent_runner = self.create_agent_runner(spec, repository)
        except Cancelled as exception:
            self._report_cancelled(spec, exception.worktree)
            return Outcome.SKIPPED
        except LoopError as exception:
            spec.comment(f"dev: {exception}")
            self._tracker.update_spec(spec)
            return Outcome.FAILED

        # A cancelled run keeps the worktree only when it holds uncommitted work.
        kept_on_cancel = False
        try:
            delivered = self._deliver_tickets(spec, agent_runner)
            pull_request_url = self.publish_pull_request(spec, repository, agent_runner.worktree.path)
            if delivered and pull_request_url is not None:
                spec.announce_delivered(pull_request_url)
                self._tracker.update_spec(spec)
            return Outcome.SUCCESS if delivered else Outcome.FAILED
        except Cancelled:
            kept_on_cancel = agent_runner.lifecycle.has_changes()
            self._report_cancelled(spec, agent_runner.worktree.path if kept_on_cancel else None)
            return Outcome.SKIPPED
        finally:
            agent_runner.exit(keep_worktree=kept_on_cancel)

    def _prepare(self, spec: Spec, repository: Repository) -> bool:
        """Publishes earlier runs' commits; True only when there are Tickets to deliver."""
        branches = self._git.branches
        branches.fetch(repository.path)
        if not spec.has_work:
            self.publish_pull_request(spec, repository, repository.path)
            return False
        if not branches.can_prepare(Branch(repository.path, spec.base_branch)):
            spec.hand_to_human(f"dev: target branch {spec.base_branch!r} does not exist on {spec.target}")
            self._tracker.update_spec(spec)
            return False
        # Worktree creation force-resets the local feature branch, so publish earlier runs' commits first.
        self.publish_pull_request(spec, repository, repository.path)
        return True

    def create_agent_runner(self, spec: Spec, repository: RepositoryData) -> AgentRunner:
        """Creates the feature-branch agent runner for `spec`."""
        return self._runner_provider.create(
            repository,
            base=spec.base_branch,
            branch=spec.feature_branch,
            hooks=self._hooks,
        )

    def _deliver_tickets(self, spec: Spec, runner: AgentRunner) -> bool:
        """Delivers each actionable Ticket in order; stops at the first failure."""
        for ticket in spec.tickets:
            if not self._deliver_ticket(spec, ticket, runner):
                return False
        return True

    def _deliver_ticket(self, spec: Spec, ticket: Ticket, runner: AgentRunner) -> bool:
        """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
        worktree = runner.worktree.path
        identifier = WorkIdentifier(spec.initiative, ticket.number)
        while True:
            head_before = self._git.commits.head(worktree)
            args = prompt_args(
                ticket,
                identifier,
                initiative_commits(self._git.commits, worktree, spec.base_branch, spec.initiative),
                worktree,
                spec.base_branch,
                spec.feature_branch,
            )
            outcome = self._run(runner, Prompt(self._prompts.dev, args))
            attempt = (
                outcome if isinstance(outcome, str) else self._validate(runner, identifier, head_before, outcome)
            )
            if isinstance(attempt, DevResult):
                spec.close_ticket(
                    ticket.number, f"Delivered in {attempt.commit}.\n\n{attempt.summary}\n\n{attempt.verification}"
                )
                self._tracker.update_spec(spec)
                self._store.reset(ticket.url)
                return True
            self._git.commits.restore(head_before)
            if self._record_failure(ticket) >= MAX_TICKET_FAILURES:
                spec.escalate(ticket.number, attempt)
                self._tracker.update_spec(spec)
                return False

    def _run(self, runner: AgentRunner, prompt: Prompt) -> AgentResult | str:
        """Runs the agent once; returns its outcome, or the failure reason."""
        try:
            return runner.run(prompt.template, prompt.args).result
        except Cancelled:
            raise
        except LoopError as exception:
            return str(exception)

    def _validate(
        self, runner: AgentRunner, identifier: WorkIdentifier, head_before: Commit, outcome: AgentResult
    ) -> DevResult | str:
        """Validates the agent's response and Git; returns the accepted result, or the failure reason."""
        try:
            dev_result = parse_response(outcome.response, identifier)
        except DevResultError as exception:
            return str(exception)
        if dev_result.status == "failed":
            return dev_result.reason or ""
        if not outcome.success:
            return "agent process did not exit successfully"

        violation = self._commit_violation(runner.worktree, head_before, identifier, dev_result)
        return dev_result if violation is None else violation

    def _commit_violation(
        self, worktree: Worktree, head_before: Commit, identifier: WorkIdentifier, dev_result: DevResult
    ) -> str | None:
        """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
        commits = self._git.commits
        head = commits.head(worktree.path)
        if head.sha == head_before.sha:
            return "HEAD did not change: the agent made no commit"
        new_commits = commits.since(head_before)
        if len(new_commits) != 1:
            return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
        prefix = identifier.to_subject()
        if not head.subject.startswith(prefix):
            return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
        if not self._git.worktrees.is_clean(worktree):
            return "the worktree has uncommitted changes"
        if dev_result.commit != head.sha:
            return f"result.commit {dev_result.commit!r} does not equal HEAD {head.sha!r}"
        return None

    def _record_failure(self, ticket: Ticket) -> int:
        """Counts one failure for `ticket` and returns its persisted total."""
        owner, repo = self._repository_pool.harness.owner_repo.split("/", 1)
        self._store.record_failure(
            ticket.url,
            owner=owner,
            repo=repo,
            task_id=str(ticket.number),
            title=ticket.title,
            items=[ticket.number],
        )
        return self._store.failed_attempts(ticket.url)

    def publish_pull_request(self, spec: Spec, repository: Repository, pusher: Path) -> str | None:
        """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

        Returns the PR URL, or None when there was nothing to publish.
        """
        if not self._git.branches.push(Branch(pusher, spec.feature_branch), Branch(pusher, spec.base_branch)):
            return None
        return repository.pull_requests.publish_draft(spec, spec.feature_branch)

    @staticmethod
    def _report_cancelled(spec: Spec, worktree: Path | None) -> None:
        if worktree is not None:
            logger.warning("spec #%s run cancelled; worktree kept at %s", spec.number, worktree)
        else:
            logger.info("spec #%s run cancelled", spec.number)
