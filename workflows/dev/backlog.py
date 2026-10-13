"""Route harness Specs, deliver their work, and publish the resulting branches."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from loop import AgentBuilder, BranchStrategy, GitOptions, LoopHook, LoopHookError

from .attempt import deliver_tickets
from .checkout import GitService
from .commands import CommandError
from .journal import FileExecutionStore
from .platforms import GithubFactory, PullRequestClient, TicketsTracker, github_for
from .records import Repository, RepositoryId, Spec, TicketWorkflowError as DevError
from .repositories import RepositoryWorkflow


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


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


def process_specs(repositories: Sequence[RepositoryWorkflow], deps: DevDeps) -> int:
    """Routes every non-human-blocked Spec to its configured repository and returns the exit code."""
    targets: dict[str, RepositoryWorkflow] = {}
    for workflow in repositories:
        target = str(workflow.repository.owner_name).casefold()
        if target in targets:
            raise DevError(f"duplicate repository workflow: {target}")
        targets[target] = workflow
    failed = False
    for spec in deps.tracker.specs():
        if not spec.awaiting_human:
            failed = (_process_spec(spec, targets, deps) is Outcome.FAILED) or failed
    return 1 if failed else 0


def _process_spec(spec: Spec, targets: Mapping[str, RepositoryWorkflow], deps: DevDeps) -> Outcome:
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
        if not _prepare(spec, workflow.repository, deps):
            return Outcome.SKIPPED
        return _deliver_on_worktree(spec, workflow, deps)
    except (CommandError, LoopHookError, subprocess.CalledProcessError) as exception:
        logger.error("spec #%s failed: %s", spec.number, exception)
        spec.comment(f"dev: {_describe(exception)}")
        deps.tracker.update_spec(spec)
        return Outcome.FAILED


def _prepare(spec: Spec, repository: Repository, deps: DevDeps) -> bool:
    deps.git.fetch(repository.checkout)
    if not spec.has_work:
        _publish_pull_request(spec, repository, repository.checkout, deps)
        return False
    base_branch = spec.base_branch
    if base_branch is None:
        raise DevError(f"cannot resolve base branch for Spec {spec.url}")
    if not deps.git.remote_branch_exists(repository.checkout, base_branch):
        spec.hand_to_human(f"dev: target branch {base_branch!r} does not exist on {spec.target}")
        deps.tracker.update_spec(spec)
        return False
    # Publish earlier runs' commits from the checkout before the worktree opens.
    _publish_pull_request(spec, repository, repository.checkout, deps)
    return True


def _deliver_on_worktree(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps) -> Outcome:
    feature_branch = spec.feature_branch
    if feature_branch is None:
        raise DevError(f"cannot resolve feature branch for Spec {spec.url}")
    options = GitOptions(
        root_path=workflow.repository.worktrees,
        repository_path=workflow.repository.checkout,
        strategy=BranchStrategy(feature_branch, f"origin/{spec.base_branch}"),
        loop_hooks=deps.loop_hooks,
    )
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
        )
        pull_request_url = _publish_pull_request(spec, workflow.repository, worktree.path, deps)
        if delivered and pull_request_url is not None:
            spec.announce_delivered(pull_request_url)
            deps.tracker.update_spec(spec)
    return Outcome.SUCCESS if delivered else Outcome.FAILED


def _publish_pull_request(spec: Spec, repository: Repository, pusher: Path, deps: DevDeps) -> str | None:
    base_branch, feature_branch = spec.base_branch, spec.feature_branch
    if base_branch is None or feature_branch is None:
        raise DevError(f"cannot resolve branches for Spec {spec.url}")
    if not deps.git.push_if_ahead(pusher, feature_branch, base_branch):
        return None
    client = PullRequestClient(github_for(repository, deps.github_factory))
    title = f"{spec.initiative}: {spec.bare_title}"
    return client.create_draft_pull_request(feature_branch, base_branch, title).url


def _describe(exception: Exception) -> str:
    if isinstance(exception, subprocess.CalledProcessError):
        return f"{exception}\n{exception.stderr or ''}".strip()
    return str(exception)
