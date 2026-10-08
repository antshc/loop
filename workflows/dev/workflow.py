"""Spec orchestration: prepare, deliver, publish, and announce each open Spec."""

from __future__ import annotations

import logging
from enum import Enum, auto
from pathlib import Path

from loop import AgentRunner, AgentRunnerProvider, Branch, Cancelled, LoopError, RepositoryData
from workflows.platforms.work_tracking import Repository, Spec

from .delivery import deliver_tickets
from .deps import DevDeps

logger = logging.getLogger("workflow.dev")


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


class DevWorkflow:
    """Delivers every open Spec through the dependencies it is constructed with."""

    def __init__(self, deps: DevDeps) -> None:
        self._deps = deps
        self._tracker = deps.tracker
        self._repository_pool = deps.repository_pool
        self._git = deps.git
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
            runner = self.create_agent_runner(spec, repository)
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
            delivered = deliver_tickets(spec, runner, self._deps)
            pull_request_url = self.publish(spec, repository, runner.worktree.path)
            if delivered and pull_request_url is not None:
                spec.announce_delivered(pull_request_url)
                self._tracker.update_spec(spec)
            return Outcome.SUCCESS if delivered else Outcome.FAILED
        except Cancelled:
            kept_on_cancel = runner.lifecycle.has_changes()
            self._report_cancelled(spec, runner.worktree.path if kept_on_cancel else None)
            return Outcome.SKIPPED
        finally:
            runner.exit(keep_worktree=kept_on_cancel)

    def _prepare(self, spec: Spec, repository: Repository) -> bool:
        """Publishes earlier runs' commits; True only when there are Tickets to deliver."""
        branches = self._git.branches
        branches.fetch(repository.path)
        if not spec.has_work:
            self.publish(spec, repository, repository.path)
            return False
        if not branches.can_prepare(Branch(repository.path, spec.base_branch)):
            spec.hand_to_human(f"dev: target branch {spec.base_branch!r} does not exist on {spec.target}")
            self._tracker.update_spec(spec)
            return False
        # Worktree creation force-resets the local feature branch, so publish earlier runs' commits first.
        self.publish(spec, repository, repository.path)
        return True

    def create_agent_runner(self, spec: Spec, repository: RepositoryData) -> AgentRunner:
        """Creates the feature-branch agent runner for `spec`."""
        return self._runner_provider.create(
            repository,
            base=spec.base_branch,
            branch=spec.feature_branch,
            hooks=tuple(self._deps.hooks),
        )

    def publish(self, spec: Spec, repository: Repository, pusher: Path) -> str | None:
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
