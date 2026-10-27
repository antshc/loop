"""Route harness Specs, deliver their work, and publish the resulting branches."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping
from enum import Enum, auto
from pathlib import Path

from loop import BranchStrategy, GitOptions, Kind, LoopHookError, Phases

from workflows.shared.agents.launcher import AgentLauncher
from workflows.shared.git import GitClient
from workflows.shared.github.pull_requests import PullRequests

from ..chore.cleanup import ChoreRefiner
from ..deliver.tickets import TicketDeliverer
from ..model.definition import Owner
from ..model.outcomes import SKIPPED, ChoreReport, Delivery
from ..model.records import Repository, Spec, TicketWorkflowError as DevError
from ..model.repositories import GithubFactory, Harness, RepositoryWorkflow, Targets, Tracker
from ..model.settings import DevSettings
from ..plan.planner import Planner
from ..review.reviewer import Finalizer, Reviewer
from ..verify.verifier import AppVerifier


logger = logging.getLogger("workflow.dev")


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


class PullRequestPublisher:
    """Pushes a Spec's feature branch when it is ahead and opens its draft pull request."""

    def __init__(self, git: GitClient, github_for: GithubFactory, phases: Phases) -> None:
        self._git = git
        self._github_for = github_for
        self._phases = phases

    def publish(self, spec: Spec, repository: Repository, pusher: Path) -> str | None:
        """The draft PR's URL, or None when there was nothing new to push."""
        base_branch, feature_branch = spec.base_branch, spec.feature_branch
        if base_branch is None or feature_branch is None:
            raise DevError(f"cannot resolve branches for Spec {spec.url}")
        with self._phases.phase("publish", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            if not self._git.push_if_ahead(pusher, feature_branch, base_branch):
                phase.skip("No new commits to publish")
                return None
            pulls = PullRequests(self._github_for(repository))
            title = f"{spec.initiative}: {spec.bare_title}"
            pull_request = pulls.create_draft_pull_request(feature_branch, base_branch, title, pull_request_body(spec))
            phase.set(pull_request=pull_request.url)
            return pull_request.url


def pull_request_body(spec: Spec) -> str:
    """The initiative ID on the first line (the code-review initiative gate reads it), then every Ticket and whether it is delivered."""
    lines = [spec.initiative, "", f"Spec: {spec.url}", "", "Tickets:"]
    lines += [f"- [{'x' if ticket.state == 'closed' else ' '}] #{ticket.number} {ticket.title}" for ticket in spec.tickets]
    return "\n".join(lines)


class SpecRouter:
    """Routes every non-human-blocked Spec to its configured repository and drives it through plan, deliver, verify, chore, review."""

    def __init__(
        self,
        settings: DevSettings,
        harness: Harness,
        targets: Targets,
        tracker: Tracker,
        git: GitClient,
        new_agent: AgentLauncher,
        phases: Phases,
        planner: Planner,
        deliverer: TicketDeliverer,
        verifier: AppVerifier,
        chore: ChoreRefiner,
        reviewer: Reviewer,
        finalizer: Finalizer,
        publisher: PullRequestPublisher,
        github_for: GithubFactory,
    ) -> None:
        self._settings = settings
        self._harness = harness
        self._targets = targets
        self._tracker = tracker
        self._git = git
        self._new_agent = new_agent
        self._phases = phases
        self._planner = planner
        self._deliverer = deliverer
        self._verifier = verifier
        self._chore = chore
        self._reviewer = reviewer
        self._finalizer = finalizer
        self._publisher = publisher
        self._github_for = github_for

    def run(self) -> int:
        """Processes every open Spec (only `settings.spec_number` when set) and returns the exit code."""
        targets = self._targets.by_slug()
        spec_number = self._settings.spec_number
        specs = [spec for spec in self._tracker.specs() if spec_number in (None, spec.number)]
        if spec_number is not None and not specs:
            logger.error("no open Spec #%s on %s", spec_number, self._harness.owner_name)
            return 1
        failed = False
        for spec in specs:
            if not spec.awaiting_human:
                failed = (self._process(spec, targets) is Outcome.FAILED) or failed
        return 1 if failed else 0

    def _process(self, spec: Spec, targets: Mapping[str, RepositoryWorkflow]) -> Outcome:
        # The engineer's request is the root of the run's trace.
        with self._phases.phase("spec", Kind.ENGINEER, Owner.ENGINEER, spec=spec.number, initiative=spec.initiative) as phase:
            outcome = self._route(spec, targets)
            if outcome is Outcome.FAILED:
                phase.fail("Spec delivery failed")
            elif outcome is Outcome.SKIPPED:
                phase.skip("Spec needs no delivery or is blocked")
            return outcome

    def _route(self, spec: Spec, targets: Mapping[str, RepositoryWorkflow]) -> Outcome:
        target = spec.target
        if target is None or spec.base_branch is None:
            spec.block(f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
            self._tracker.update_spec(spec)
            return Outcome.SKIPPED
        workflow = targets.get(target.casefold())
        if workflow is None:
            spec.block(f"dev: repo:target:{target} is not configured for harness {self._harness.owner_name}")
            self._tracker.update_spec(spec)
            return Outcome.SKIPPED
        try:
            if not self._prepare(spec, workflow):
                return Outcome.FAILED if spec.awaiting_human and spec.needs_plan else Outcome.SKIPPED
            return self._deliver_on_worktree(spec, workflow)
        except (LoopHookError, subprocess.CalledProcessError) as exception:
            logger.error("Spec #%s failed (%s)", spec.number, type(exception).__name__)
            logger.debug("Spec failure details: %s", exception, exc_info=True)
            spec.comment(f"dev: {_describe(exception)}")
            self._tracker.update_spec(spec)
            return Outcome.FAILED

    def _prepare(self, spec: Spec, workflow: RepositoryWorkflow) -> bool:
        repository, git, phases = workflow.repository, self._git, self._phases
        with phases.phase("preflight", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            git.fetch(repository.checkout)
            base_branch = spec.base_branch
            if base_branch is None:
                raise DevError(f"cannot resolve base branch for Spec {spec.url}")
            if spec.needs_plan and workflow.run_plan is not None:
                if not git.remote_branch_exists(repository.checkout, base_branch):
                    self._hand_over_missing_base(spec, phase, base_branch)
                    return False
                if not self._planner.plan(spec, repository.checkout, workflow.run_plan):
                    phase.fail("Planning failed; human input required")
                    return False
            else:
                reason = "No planner configured" if workflow.run_plan is None else "Spec already has Tickets"
                _skip(phases, "plan", Kind.AGENT, Owner.PLANNER, reason)
                _skip(phases, "create-tickets", Kind.CODE, Owner.CODE, reason)
            if not spec.has_work:
                self._publisher.publish(spec, repository, repository.checkout)
                return False
            if not git.remote_branch_exists(repository.checkout, base_branch):
                self._hand_over_missing_base(spec, phase, base_branch)
                return False
            # Publish earlier runs' commits from the checkout before the worktree opens.
            self._publisher.publish(spec, repository, repository.checkout)
            return True

    def _hand_over_missing_base(self, spec: Spec, phase, base_branch: str) -> None:
        spec.hand_to_human(f"dev: target branch {base_branch!r} does not exist on {spec.target}")
        self._tracker.update_spec(spec)
        phase.fail("base branch missing")

    def _deliver_on_worktree(self, spec: Spec, workflow: RepositoryWorkflow) -> Outcome:
        feature_branch = spec.feature_branch
        if feature_branch is None:
            raise DevError(f"cannot resolve feature branch for Spec {spec.url}")
        repository, git, phases = workflow.repository, self._git, self._phases
        options = GitOptions(
            root_path=repository.worktrees,
            repository_path=repository.checkout,
            strategy=BranchStrategy(feature_branch, f"origin/{spec.base_branch}"),
            loop_hooks=self._settings.loop_hooks,
        )
        delivery: Delivery | None = None
        published: list[str] = []

        def publish(worktree_path: Path) -> None:
            url = self._publisher.publish(spec, repository, worktree_path)
            if url is not None:
                published.append(url)

        with phases.phase("deliver", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            with self._new_agent().with_git(options).open() as worktree:
                delivered = self._deliverer.deliver(
                    spec, worktree, workflow.run_agents, publish=lambda: publish(worktree.path)
                )
                if not delivered:
                    phase.fail("Ticket delivery exhausted its retries")
                publish(worktree.path)
                # Tickets are published as they land, so the last publish may have found nothing new to push.
                pull_request_url = published[-1] if published else None
                if delivered and pull_request_url is not None:
                    spec.announce_delivered(pull_request_url)
                    self._tracker.update_spec(spec)
                    verification = SKIPPED
                    if workflow.run_verify is not None:
                        verification = self._verifier.verify(spec, pull_request_url, worktree, workflow.run_verify)
                    else:
                        _skip(phases, "verify", Kind.AGENT, Owner.VERIFIER, "No verifier configured")
                    chore: ChoreReport | None = None
                    if workflow.run_chore is not None:
                        chore = self._chore.refine(
                            spec, pull_request_url, worktree, workflow.run_chore, self._github_for(repository)
                        )
                    else:
                        _skip(phases, "chore", Kind.AGENT, Owner.CHORE, "No cleanup configured")
                    delivery = Delivery(
                        pull_request_url,
                        git.head(worktree.path).sha,
                        delivered,
                        tuple(
                            f"{commit.sha[:7]} {commit.subject}"
                            for commit in git.branch_commits(worktree.path, spec.base_branch or "")
                        ),
                        verification,
                        chore,
                    )
        # The worktree is closed (pushed and clean) before review, so the reviewer never sees implementation state.
        if delivery is not None and workflow.run_review is not None:
            outcome = self._reviewer.review(spec, delivery, repository, workflow.run_review)
            ready = self._finalizer.finalize(spec, delivery, outcome, repository)
            return Outcome.SUCCESS if ready else Outcome.FAILED
        if delivery is not None:
            _skip(phases, "review", Kind.AGENT, Owner.REVIEWER, "No reviewer configured")
            _skip(phases, "finalize", Kind.CODE, Owner.CODE, "PR remains a draft without review")
            if delivery.verification.blocks_ready:
                return Outcome.FAILED
        return Outcome.SUCCESS if delivered else Outcome.FAILED


def _describe(exception: Exception) -> str:
    if isinstance(exception, subprocess.CalledProcessError):
        return f"{exception}\n{exception.stderr or ''}".strip()
    return str(exception)


def _skip(phases: Phases, name: str, kind: str, owner: str, reason: str) -> None:
    with phases.phase(name, kind, owner) as phase:
        phase.skip(reason)
