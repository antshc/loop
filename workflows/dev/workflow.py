"""Spec orchestration: prepare, deliver, publish, and announce each open Spec."""

from __future__ import annotations

import logging
import subprocess
from enum import Enum, auto
from pathlib import Path

from loop import DEFAULT, AgentClient, AgentRequest, BranchStrategy, GitOptions, LoopHookError
from workflows.platforms.git import Commit
from workflows.platforms.process import CommandError
from workflows.platforms.work_tracking import Repository, Spec, Ticket, WorkIdentifier

from .deps import DevDeps
from .errors import DevError
from .prompting import prompt_args, render_prompt
from .result import DevResult, parse_response
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
        self._new_agent = deps.new_agent
        self._loop_hooks = deps.loop_hooks
        self._prompts = deps.prompts

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
            return self._deliver_on_worktree(spec, repository)
        except (CommandError, LoopHookError, subprocess.CalledProcessError) as exception:
            logger.error("spec #%s failed: %s", spec.number, exception)
            spec.comment(f"dev: {self._describe(exception)}")
            self._tracker.update_spec(spec)
            return Outcome.FAILED

    def _prepare(self, spec: Spec, repository: Repository) -> bool:
        """Publishes earlier runs' commits; True only when there are Tickets to deliver."""
        self._git.fetch(repository.path)
        if not spec.has_work:
            self.publish_pull_request(spec, repository, repository.path)
            return False
        if not self._git.remote_branch_exists(repository.path, spec.base_branch):
            spec.hand_to_human(f"dev: target branch {spec.base_branch!r} does not exist on {spec.target}")
            self._tracker.update_spec(spec)
            return False
        # Publish earlier runs' commits from the checkout before the worktree opens.
        self.publish_pull_request(spec, repository, repository.path)
        return True

    def _deliver_on_worktree(self, spec: Spec, repository: Repository) -> Outcome:
        """Opens the feature-branch worktree, delivers the Tickets in it, and publishes before it closes."""
        options = GitOptions(
            root_path=repository.worktree_root,
            repository_path=repository.path,
            strategy=BranchStrategy(spec.feature_branch, f"origin/{spec.base_branch}"),
            loop_hooks=self._loop_hooks,
        )
        with self._new_agent().with_git(options).open() as worktree:
            client = worktree.agent(DEFAULT)
            delivered = self._deliver_tickets(spec, worktree.path, client)
            pull_request_url = self.publish_pull_request(spec, repository, worktree.path)
            if delivered and pull_request_url is not None:
                spec.announce_delivered(pull_request_url)
                self._tracker.update_spec(spec)
        return Outcome.SUCCESS if delivered else Outcome.FAILED

    def _deliver_tickets(self, spec: Spec, worktree: Path, client: AgentClient) -> bool:
        """Delivers each actionable Ticket in order; stops at the first failure."""
        for ticket in spec.tickets:
            if not self._deliver_ticket(spec, ticket, worktree, client):
                return False
        return True

    def _deliver_ticket(self, spec: Spec, ticket: Ticket, worktree: Path, client: AgentClient) -> bool:
        """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
        identifier = WorkIdentifier(spec.initiative, ticket.number)
        while True:
            head_before = self._git.head(worktree)
            attempt = self._attempt(spec, ticket, identifier, worktree, client, head_before)
            if isinstance(attempt, DevResult):
                spec.close_ticket(
                    ticket.number, f"Delivered in {attempt.commit}.\n\n{attempt.summary}\n\n{attempt.verification}"
                )
                self._tracker.update_spec(spec)
                self._store.reset(ticket.url)
                return True
            self._git.restore(worktree, head_before.sha)
            if self._record_failure(ticket) >= MAX_TICKET_FAILURES:
                spec.escalate(ticket.number, attempt)
                self._tracker.update_spec(spec)
                return False

    def _initiative_commits(self, worktree: Path, spec: Spec) -> list[str]:
        """This Initiative's `ccode(<initiative-id>|` commits on the feature branch since the base branch, as `<short hash> <subject>`."""
        found = self._git.initiative_commits(worktree, spec.base_branch, f"ccode({spec.initiative}|")
        return [f"{commit.sha[:7]} {commit.subject}" for commit in found]

    def _attempt(
        self,
        spec: Spec,
        ticket: Ticket,
        identifier: WorkIdentifier,
        worktree: Path,
        client: AgentClient,
        head_before: Commit,
    ) -> DevResult | str:
        """Runs the agent once and validates its response and Git; returns the accepted result, or the failure reason."""
        args = prompt_args(
            ticket,
            identifier,
            self._initiative_commits(worktree, spec),
            worktree,
            spec.base_branch,
            spec.feature_branch,
        )
        try:
            prompt = render_prompt(self._prompts.dev, args)
            output = client.run(AgentRequest(prompt)).output
            dev_result = parse_response(output, identifier)
        except subprocess.CalledProcessError:
            return "agent process did not exit successfully"
        except DevError as exception:
            return str(exception)
        if dev_result.status == "failed":
            return dev_result.reason or ""

        violation = self._commit_violation(worktree, head_before, identifier, dev_result)
        return dev_result if violation is None else violation

    def _commit_violation(
        self, worktree: Path, head_before: Commit, identifier: WorkIdentifier, dev_result: DevResult
    ) -> str | None:
        """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
        head = self._git.head(worktree)
        if head.sha == head_before.sha:
            return "HEAD did not change: the agent made no commit"
        new_commits = self._git.commits_since(worktree, head_before.sha)
        if len(new_commits) != 1:
            return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
        prefix = identifier.to_subject()
        if not head.subject.startswith(prefix):
            return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
        if not self._git.is_clean(worktree):
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
        if not self._git.push_if_ahead(pusher, spec.feature_branch, spec.base_branch):
            return None
        return repository.pull_requests.publish_draft(spec, spec.feature_branch)

    @staticmethod
    def _describe(exception: Exception) -> str:
        if isinstance(exception, subprocess.CalledProcessError):
            return f"{exception}\n{exception.stderr or ''}".strip()
        return str(exception)
