from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loop.errors import LoopError
from loop.platforms.git import Branch, BranchService, CommitService, Hook, Worktree, WorktreeService


@dataclass(frozen=True)
class LifecycleResult[T]:
    result: T
    branch: str
    commits: tuple[str, ...]


def run_host_hooks(
    worktrees: WorktreeService, hooks: Sequence[Hook], worktree: Path, *, cancel: threading.Event | None = None
) -> None:
    """Run each Hook on the host in the worktree, in order; the first failure stops the rest."""
    for hook in hooks:
        worktrees.run_hook(hook, worktree, cancel)


def run_lifecycle[T](
    commits: CommitService,
    branches: BranchService,
    worktrees: WorktreeService,
    checkout: Path,
    worktree: Worktree,
    work: Callable[[str], T],
    *,
    branch: str | None = None,
    keep_source_branch: bool = False,
) -> LifecycleResult[T]:
    """Wrap one unit of agent work with the base head before and commit collection after.

    With `branch=None` the worktree is on a temp branch that is merged into the host checkout's
    current branch afterwards; otherwise the commits stay on `branch`.
    """
    host = worktrees.get(checkout) if branch is None else None
    host_branch = host.branch.name if host is not None and host.branch is not None else None
    if branch is None and host_branch is None:
        raise LoopError(f"cannot merge into a detached HEAD in {checkout}")
    if worktree.branch is None:
        raise LoopError(f"worktree is on a detached HEAD: {worktree.path}")
    worktree_branch = worktree.branch.name

    base_head = commits.head(worktree.path)
    result = work(base_head.sha)

    new_commits = commits.since(base_head)
    if branch is None:
        branches.merge(checkout, Branch(checkout, worktree_branch))
        if not keep_source_branch:
            worktrees.detach(worktree)
            branches.delete(Branch(checkout, worktree_branch))
    return LifecycleResult(result, worktree_branch, tuple(commit.sha for commit in new_commits))
