"""Git acceptance check for a `completed` Ticket run."""

from __future__ import annotations

from pathlib import Path

from loop import Commit, GitClient

from .result import DevResult


def commit_violation(git: GitClient, worktree: Path, head_before: str, identifier: str, dev_result: DevResult) -> str | None:
    """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
    head = git.head(worktree)
    if head == head_before:
        return "HEAD did not change: the agent made no commit"
    new_commits = git.commits_between(worktree, head_before, head)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before}, found {len(new_commits)}"
    prefix = Commit.subject_prefix(identifier)
    subject = git.head_subject(worktree)
    if not subject.startswith(prefix):
        return f"HEAD subject {subject!r} does not start with {prefix!r}"
    if not git.is_clean(worktree):
        return "the worktree has uncommitted changes"
    if dev_result.commit != head:
        return f"result.commit {dev_result.commit!r} does not equal HEAD {head!r}"
    return None
