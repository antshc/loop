"""Git acceptance check for a `completed` Ticket run."""

from __future__ import annotations

from pathlib import Path

from loop import Commit, CommitService, GitClient

from .commit_tag import subject_prefix
from .result import DevResult


def commit_violation(
    git: GitClient, commits: CommitService, worktree: Path, head_before: Commit, identifier: str, dev_result: DevResult
) -> str | None:
    """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
    head = commits.head(worktree)
    if head.sha == head_before.sha:
        return "HEAD did not change: the agent made no commit"
    new_commits = commits.since(head_before)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
    prefix = subject_prefix(identifier)
    if not head.subject.startswith(prefix):
        return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
    if not git.is_clean(worktree):
        return "the worktree has uncommitted changes"
    if dev_result.commit != head.sha:
        return f"result.commit {dev_result.commit!r} does not equal HEAD {head.sha!r}"
    return None
