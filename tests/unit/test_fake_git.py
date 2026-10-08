from __future__ import annotations

from pathlib import Path

from loop import Branch, Commit, Worktree
from loop.testing import FakeGit


def test_a_commit_is_visible_to_the_branch_worktree_and_commit_services() -> None:
    git = FakeGit()
    target = Path("/repo.worktrees/feature-x")
    branch = Branch(target, "feature-x")
    git.worktrees.create(branch, target)

    sha = git.commit(target, "add x")

    assert git.commits.head(target) == Commit(target, sha, "add x")
    assert git.worktrees.has_changes(Worktree(target, branch)) is True
    assert git.branches.ahead_of_remote(branch, Branch(target, "main")) is True
