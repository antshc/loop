"""Factory-owned merge preparation, conflict resolution, verification and publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from loop import AgentWorkspace, Evidence, Fail, Nothing, Pass, Stage, Verify
from loop.shared.git import GitClient

from workflows.software_factory.agents.roles.maintainer import (
    RESOLVE_CONFLICTS,
    ConflictRequest,
    Fixed,
)
from workflows.software_factory.events.pull_requests import (
    MergeConflict,
    MergePlanned,
    MergePrepared,
    MergePublished,
    MergeVerified,
    PullRequestPolled,
)
from workflows.software_factory.infrastructure.github.account import GitHub
from workflows.software_factory.infrastructure.github.pull_requests.model import (
    PullRequest,
)
from workflows.software_factory.infrastructure.github.repo import CommandError


@dataclass(frozen=True)
class PlanInput:
    polled: PullRequestPolled
    conflict: MergeConflict
    worktree: AgentWorkspace
    git: GitClient
    github: GitHub


@dataclass(frozen=True)
class MergeInput:
    polled: PullRequestPolled
    plan: MergePlanned
    worktree: AgentWorkspace
    git: GitClient
    github: GitHub


@dataclass(frozen=True)
class ResolveInput(MergeInput):
    prepared: MergePrepared


@dataclass(frozen=True)
class PublishInput(MergeInput):
    verified: MergeVerified


def plan() -> Stage[PlanInput, MergePlanned, MergePlanned]:
    """Pins revisions durably before starting a merge in a clean recovery worktree."""
    return (
        Stage("plan-merge", PlanInput, produces=MergePlanned)
        .effect(_plan, reconcile=lambda _: None)
        .gate(lambda _, planned, __: Pass(planned))
    )


def prepare() -> Stage[MergeInput, MergePrepared, MergePrepared]:
    """Starts a non-committing merge; interrupted dirty merges require inspection."""
    return (
        Stage("prepare-merge", MergeInput, produces=MergePrepared)
        .effect(_prepare, reconcile=_resume_prepare)
        .gate(lambda _, prepared, __: Pass(prepared))
    )


def resolve() -> Stage[ResolveInput, Fixed, Nothing]:
    """Maintainer edits only conflicted merges; Python checks HEAD and merge metadata."""
    # Factory guards retain merges; the runtime's commit contracts assume one parent.
    return (
        Stage("resolve-conflicts", ResolveInput, produces=Nothing)
        .agent(
            RESOLVE_CONFLICTS,
            request=lambda i, _: ConflictRequest(
                i.polled.snapshot,
                i.plan.start,
                i.plan.base_ref,
                i.plan.base,
                i.prepared.paths,
            ),
        )
        .gate(_resolved)
        .when(lambda i: bool(i.prepared.paths))
    )


def verify(*, checks: Verify) -> Stage[MergeInput, MergeVerified | Fail, MergeVerified]:
    """Stages resolutions and verifies the exact tree, retaining all failed work."""
    return (
        Stage("verify-merge", MergeInput, produces=MergeVerified)
        .effect(lambda i: _verify(i, checks), reconcile=lambda _: None)
        .gate(
            lambda _, result, __: result if isinstance(result, Fail) else Pass(result)
        )
    )


def publish(
    *, message: Callable[[PublishInput], str]
) -> Stage[PublishInput, MergePublished, MergePublished]:
    """Commits and publishes the verified merge, adopting an exact interrupted commit."""
    return (
        Stage("publish-merge", PublishInput, produces=MergePublished)
        .effect(
            lambda i: _publish(i, message(i)),
            reconcile=lambda i: _publish(i, message(i)),
        )
        .gate(lambda _, published, __: Pass(published))
    )


def _plan(i: PlanInput) -> MergePlanned:
    path, conflict = i.worktree.path, i.conflict
    if not i.git.is_clean(path) or _merge_head(i.git, i.worktree):
        raise ValueError("Recovery worktree has unfinished work; inspect before retry")
    planned = MergePlanned(
        conflict.head, i.git.head(path).sha, conflict.base_ref, conflict.base
    )
    _current(i.polled, planned, i.github, {planned.head})
    i.git.fetch_revision(path, planned.base)
    if not i.git.is_ancestor(path, planned.head, planned.start):
        raise ValueError("Recovery branch no longer contains the PR head")
    return planned


def _prepare(i: MergeInput) -> MergePrepared:
    _unchanged(i)
    if not i.git.is_clean(i.worktree.path) or _merge_head(i.git, i.worktree):
        raise ValueError("Unfinished merge retained; inspect before retry")
    if i.git.is_ancestor(i.worktree.path, i.plan.base, i.plan.start):
        return MergePrepared(())
    try:
        i.git.text(i.worktree.path, "merge", "--no-commit", "--no-ff", i.plan.base)
    except CommandError as exception:
        paths = _unmerged(i)
        if exception.exit_code != 1 or not paths:
            raise
        _merging(i)
        return MergePrepared(paths)
    _merging(i)
    return MergePrepared(())


def _resume_prepare(i: MergeInput) -> MergePrepared | None:
    if _merge_head(i.git, i.worktree) or not i.git.is_clean(i.worktree.path):
        raise ValueError("Interrupted merge retained; inspect before retry")
    return None


def _resolved(i: ResolveInput, reply: Fixed, _: Evidence) -> Pass | Fail:
    _unchanged(i)
    _merging(i)
    if reply.verdict != "fixed":
        return Fail(reply.reason or "Merge intent needs clarification")
    return Pass(Nothing())


def _verify(i: MergeInput, checks: Verify) -> MergeVerified | Fail:
    _unchanged(i)
    _expected_merge(i)
    tree = _staged_tree(i)
    failed = checks(i.worktree.path)
    if failed is not None:
        return failed
    _unchanged(i)
    _expected_merge(i)
    if _staged_tree(i) != tree:
        return Fail("Verification changed the merge tree; inspect retained work")
    return MergeVerified(tree)


def _publish(i: PublishInput, message: str) -> MergePublished:
    path, git, planned = i.worktree.path, i.git, i.plan
    local = git.head(path).sha
    _current(i.polled, planned, i.github, {planned.head, local})
    if local == planned.start:
        _expected_merge(i)
        if _staged_tree(i) != i.verified.tree:
            raise ValueError("Verified merge tree changed; publication stopped")
        if _merge_head(git, i.worktree):
            local = git.commit(path, message).sha
        elif git.text(path, "rev-parse", "HEAD^{tree}") != i.verified.tree:
            raise ValueError("Merge metadata disappeared; publication stopped")
    # Recovery accepts exactly the verified two-parent commit, never its subject alone.
    _intended_commit(i, local, message)
    pull = _current(i.polled, planned, i.github, {planned.head, local})
    if pull.head_sha != local:
        git.publish(path, i.polled.branch)
    pull = i.github.pull_requests(i.polled.target.slug).pull_request(i.polled.number)
    if pull.head_sha != local:
        raise ValueError("Published merge SHA could not be confirmed")
    return MergePublished(local)


def _intended_commit(i: PublishInput, local: str, message: str) -> None:
    git, path = i.git, i.worktree.path
    if not git.is_clean(path) or _merge_head(git, i.worktree):
        raise ValueError("Merge commit left unfinished work; inspect before retry")
    if git.text(path, "rev-parse", "HEAD^{tree}") != i.verified.tree:
        raise ValueError("Merge commit differs from verified tree")
    if local == i.plan.start and git.is_ancestor(path, i.plan.base, local):
        return
    parents = git.text(path, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    if parents != [i.plan.start, i.plan.base] or git.head(path).subject != message:
        raise ValueError("Unrecognized merge commit; inspect before retry")


def _current(
    polled: PullRequestPolled,
    planned: MergePlanned,
    github: GitHub,
    heads: set[str],
) -> PullRequest:
    pull = github.pull_requests(polled.target.slug).pull_request(polled.number)
    if (
        not pull.is_open
        or pull.head_ref != polled.branch
        or pull.head_repository != polled.target.slug
        or pull.head_sha not in heads
        or pull.base_ref != planned.base_ref
        or pull.payload["base"]["sha"] != planned.base
    ):
        raise ValueError("PR head, base or state changed; merge publication stopped")
    return pull


def _unchanged(i: MergeInput) -> None:
    if i.git.head(i.worktree.path).sha != i.plan.start:
        raise ValueError("Unexpected HEAD change during conflict resolution")


def _expected_merge(i: MergeInput) -> None:
    if i.git.is_ancestor(i.worktree.path, i.plan.base, i.plan.start):
        if _merge_head(i.git, i.worktree):
            raise ValueError("Unexpected merge in recovery worktree")
    else:
        _merging(i)


def _merging(i: MergeInput) -> None:
    if _merge_head(i.git, i.worktree) != i.plan.base:
        raise ValueError("Merge metadata changed; inspect retained work")


def _merge_head(git: GitClient, workspace: AgentWorkspace) -> str:
    path = git.text(workspace.path, "rev-parse", "--git-path", "MERGE_HEAD")
    merge_head = workspace.path / path
    return merge_head.read_text().strip() if merge_head.exists() else ""


def _unmerged(i: MergeInput) -> tuple[str, ...]:
    return tuple(
        name
        for name in i.git.text(
            i.worktree.path, "diff", "--name-only", "--diff-filter=U", "-z"
        ).split("\0")
        if name
    )


def _staged_tree(i: MergeInput) -> str:
    git, path = i.git, i.worktree.path
    git.text(path, "add", "-A")
    if _unmerged(i):
        raise ValueError("Merge still has unmerged entries")
    git.text(path, "diff", "--cached", "--check")
    return git.text(path, "write-tree")
