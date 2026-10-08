"""Git acceptance check for a `completed` Ticket run."""

from __future__ import annotations

from loop import Commit, CommitService, Worktree, WorktreeService
from workflows.platforms.work_tracking import WorkIdentifier

from .result import DevResult


def commit_violation(
    worktrees: WorktreeService,
    commits: CommitService,
    worktree: Worktree,
    head_before: Commit,
    identifier: WorkIdentifier,
    dev_result: DevResult,
) -> str | None:
    """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
    head = commits.head(worktree.path)
    if head.sha == head_before.sha:
        return "HEAD did not change: the agent made no commit"
    new_commits = commits.since(head_before)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
    prefix = identifier.to_subject()
    if not head.subject.startswith(prefix):
        return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
    if not worktrees.is_clean(worktree):
        return "the worktree has uncommitted changes"
    if dev_result.commit != head.sha:
        return f"result.commit {dev_result.commit!r} does not equal HEAD {head.sha!r}"
    return None
