"""Route harness Specs, deliver their work, and publish the resulting branches."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from loop import AgentBuilder, BranchStrategy, GitOptions, Kind, LoopHook, LoopHookError, Phases

from .attempt import deliver_tickets
from .chore import ChoreReport, run_chore
from .checkout import GitService
from .commands import CommandError
from .journal import FileExecutionStore
from .definition import Owner
from .plan import plan_spec
from .platforms import GithubFactory, PullRequestClient, TicketsTracker, github_for
from .records import Repository, RepositoryId, Spec, TicketWorkflowError as DevError
from .repositories import RepositoryWorkflow
from .review import Delivery, finalize, review_pull_request
from .verify import SKIPPED, verify_app


logger = logging.getLogger("workflow.dev")


@dataclass(frozen=True)
class DevDeps:
    """Shared dependencies for routing and delivering harness Specs."""

    tracker: TicketsTracker
    store: FileExecutionStore
    git: GitService
    new_agent: Callable[[], AgentBuilder]
    loop_hooks: tuple[LoopHook, ...]
    ticket_repository: RepositoryId
    github_factory: GithubFactory | None
    max_ticket_failures: int
    spec_number: int | None = None
    phases: Phases = field(default_factory=Phases)
    # Where agents' evidence is kept, outside every worktree.
    log_dir: Path = Path(".loop")


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


def process_specs(repositories: Sequence[RepositoryWorkflow], deps: DevDeps) -> int:
    """Routes every non-human-blocked Spec (only `deps.spec_number` when set) to its configured repository and returns the exit code."""
    targets: dict[str, RepositoryWorkflow] = {}
    for workflow in repositories:
        target = str(workflow.repository.owner_name).casefold()
        if target in targets:
            raise DevError(f"duplicate repository workflow: {target}")
        targets[target] = workflow
    failed = False
    specs = [spec for spec in deps.tracker.specs() if deps.spec_number in (None, spec.number)]
    if deps.spec_number is not None and not specs:
        logger.error("no open Spec #%s on %s", deps.spec_number, deps.ticket_repository)
        return 1
    for spec in specs:
        if not spec.awaiting_human:
            failed = (_process_spec(spec, targets, deps) is Outcome.FAILED) or failed
    return 1 if failed else 0


def _process_spec(spec: Spec, targets: Mapping[str, RepositoryWorkflow], deps: DevDeps) -> Outcome:
    # The engineer's request is the root of the run's trace.
    with deps.phases.phase("spec", Kind.ENGINEER, Owner.ENGINEER, spec=spec.number, initiative=spec.initiative) as phase:
        outcome = _route_spec(spec, targets, deps)
        if outcome is Outcome.FAILED:
            phase.fail("Spec delivery failed")
        elif outcome is Outcome.SKIPPED:
            phase.skip("Spec needs no delivery or is blocked")
        return outcome


def _route_spec(spec: Spec, targets: Mapping[str, RepositoryWorkflow], deps: DevDeps) -> Outcome:
    target = spec.target
    if target is None or spec.base_branch is None:
        spec.block(f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        deps.tracker.update_spec(spec)
        return Outcome.SKIPPED
    workflow = targets.get(target.casefold())
    if workflow is None:
        spec.block(f"dev: repo:target:{target} is not configured for harness {deps.ticket_repository}")
        deps.tracker.update_spec(spec)
        return Outcome.SKIPPED
    try:
        if not _prepare(spec, workflow, deps):
            return Outcome.FAILED if spec.awaiting_human and spec.needs_plan else Outcome.SKIPPED
        return _deliver_on_worktree(spec, workflow, deps)
    except (CommandError, LoopHookError, subprocess.CalledProcessError) as exception:
        logger.error("Spec #%s failed (%s)", spec.number, type(exception).__name__)
        logger.debug("Spec failure details: %s", exception, exc_info=True)
        spec.comment(f"dev: {_describe(exception)}")
        deps.tracker.update_spec(spec)
        return Outcome.FAILED


def _prepare(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps) -> bool:
    repository = workflow.repository
    with deps.phases.phase("preflight", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
        deps.git.fetch(repository.checkout)
        base_branch = spec.base_branch
        if base_branch is None:
            raise DevError(f"cannot resolve base branch for Spec {spec.url}")
        if spec.needs_plan and workflow.run_plan is not None:
            if not deps.git.remote_branch_exists(repository.checkout, base_branch):
                spec.hand_to_human(f"dev: target branch {base_branch!r} does not exist on {spec.target}")
                deps.tracker.update_spec(spec)
                phase.fail("base branch missing")
                return False
            planned = plan_spec(
                spec, repository.checkout, workflow.run_plan,
                new_agent=deps.new_agent, git=deps.git, tracker=deps.tracker, phases=deps.phases,
            )
            if not planned:
                phase.fail("Planning failed; human input required")
                return False
        elif workflow.run_plan is None:
            _skip(deps.phases, "plan", Kind.AGENT, Owner.PLANNER, "No planner configured")
            _skip(deps.phases, "create-tickets", Kind.CODE, Owner.CODE, "No planner configured")
        else:
            _skip(deps.phases, "plan", Kind.AGENT, Owner.PLANNER, "Spec already has Tickets")
            _skip(deps.phases, "create-tickets", Kind.CODE, Owner.CODE, "Spec already has Tickets")
        if not spec.has_work:
            _publish_pull_request(spec, repository, repository.checkout, deps)
            return False
        if not deps.git.remote_branch_exists(repository.checkout, base_branch):
            spec.hand_to_human(f"dev: target branch {base_branch!r} does not exist on {spec.target}")
            deps.tracker.update_spec(spec)
            phase.fail("base branch missing")
            return False
        # Publish earlier runs' commits from the checkout before the worktree opens.
        _publish_pull_request(spec, repository, repository.checkout, deps)
        return True


def _deliver_on_worktree(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps) -> Outcome:
    feature_branch = spec.feature_branch
    if feature_branch is None:
        raise DevError(f"cannot resolve feature branch for Spec {spec.url}")
    repository = workflow.repository
    options = GitOptions(
        root_path=repository.worktrees,
        repository_path=repository.checkout,
        strategy=BranchStrategy(feature_branch, f"origin/{spec.base_branch}"),
        loop_hooks=deps.loop_hooks,
    )
    delivery: Delivery | None = None
    published: list[str] = []

    def publish(worktree_path: Path) -> None:
        url = _publish_pull_request(spec, repository, worktree_path, deps)
        if url is not None:
            published.append(url)

    with deps.phases.phase("deliver", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
        with deps.new_agent().with_git(options).open() as worktree:
            delivered = deliver_tickets(
                spec,
                worktree,
                workflow.run_agents,
                git=deps.git,
                tracker=deps.tracker,
                store=deps.store,
                ticket_repository=deps.ticket_repository,
                max_failures=deps.max_ticket_failures,
                phases=deps.phases,
                publish=lambda: publish(worktree.path),
            )
            if not delivered:
                phase.fail("Ticket delivery exhausted its retries")
            publish(worktree.path)
            # Tickets are published as they land, so the last publish may have found nothing new to push.
            pull_request_url = published[-1] if published else None
            if delivered and pull_request_url is not None:
                spec.announce_delivered(pull_request_url)
                deps.tracker.update_spec(spec)
                verification = SKIPPED
                if workflow.run_verify is not None:
                    verification = verify_app(
                        spec, pull_request_url, worktree, workflow.run_verify,
                        git=deps.git, tracker=deps.tracker, phases=deps.phases, evidence_root=deps.log_dir / "evidence",
                    )
                if workflow.run_verify is None:
                    _skip(deps.phases, "verify", Kind.AGENT, Owner.VERIFIER, "No verifier configured")
                chore: ChoreReport | None = None
                if workflow.run_chore is not None:
                    chore = run_chore(
                        spec, pull_request_url, worktree, workflow.run_chore,
                        git=deps.git, github=github_for(repository, deps.github_factory), phases=deps.phases,
                    )
                else:
                    _skip(deps.phases, "chore", Kind.AGENT, Owner.CHORE, "No cleanup configured")
                delivery = Delivery(
                    pull_request_url,
                    deps.git.head(worktree.path).sha,
                    delivered,
                    tuple(
                        f"{commit.sha[:7]} {commit.subject}"
                        for commit in deps.git.branch_commits(worktree.path, spec.base_branch or "")
                    ),
                    verification,
                    chore,
                )
    # The worktree is closed (pushed and clean) before review, so the reviewer never sees implementation state.
    if delivery is not None and workflow.run_review is not None:
        outcome = review_pull_request(
            spec, delivery, repository, workflow.run_review,
            new_agent=deps.new_agent, git=deps.git, phases=deps.phases,
        )
        ready = finalize(
            spec, delivery, outcome, repository,
            git=deps.git, pulls=PullRequestClient(github_for(repository, deps.github_factory)),
            tracker=deps.tracker, phases=deps.phases,
        )
        return Outcome.SUCCESS if ready else Outcome.FAILED
    if delivery is not None:
        _skip(deps.phases, "review", Kind.AGENT, Owner.REVIEWER, "No reviewer configured")
        _skip(deps.phases, "finalize", Kind.CODE, Owner.CODE, "PR remains a draft without review")
        if delivery.verification.blocks_ready:
            return Outcome.FAILED
    return Outcome.SUCCESS if delivered else Outcome.FAILED


def _publish_pull_request(spec: Spec, repository: Repository, pusher: Path, deps: DevDeps) -> str | None:
    base_branch, feature_branch = spec.base_branch, spec.feature_branch
    if base_branch is None or feature_branch is None:
        raise DevError(f"cannot resolve branches for Spec {spec.url}")
    with deps.phases.phase("publish", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
        if not deps.git.push_if_ahead(pusher, feature_branch, base_branch):
            phase.skip("No new commits to publish")
            return None
        client = PullRequestClient(github_for(repository, deps.github_factory))
        title = f"{spec.initiative}: {spec.bare_title}"
        pull_request = client.create_draft_pull_request(feature_branch, base_branch, title, _pull_request_body(spec))
        phase.set(pull_request=pull_request.url)
        return pull_request.url


def _pull_request_body(spec: Spec) -> str:
    """The initiative ID on the first line (the code-review initiative gate reads it), then every Ticket and whether it is delivered."""
    lines = [spec.initiative, "", f"Spec: {spec.url}", "", "Tickets:"]
    lines += [f"- [{'x' if ticket.state == 'closed' else ' '}] #{ticket.number} {ticket.title}" for ticket in spec.tickets]
    return "\n".join(lines)


def _describe(exception: Exception) -> str:
    if isinstance(exception, subprocess.CalledProcessError):
        return f"{exception}\n{exception.stderr or ''}".strip()
    return str(exception)


def _skip(phases: Phases, name: str, kind: str, owner: str, reason: str) -> None:
    with phases.phase(name, kind, owner) as phase:
        phase.skip(reason)
