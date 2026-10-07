from __future__ import annotations

from pathlib import Path

from conftest import FakeRunner
from loop import BranchService, CommitService, Git, WorktreeService
from loop.testing import FakeGit


def test_git_groups_services_over_one_shared_client_and_runner() -> None:
    runner = FakeRunner()
    git = Git(run=runner)

    assert isinstance(git.branches, BranchService)
    assert isinstance(git.worktrees, WorktreeService)
    assert isinstance(git.commits, CommitService)
    assert git.branches._git is git.worktrees._git

    git.commits.identity(Path("/repo"))

    assert [call[0] for call in runner.calls] == [
        "git config --get user.name",
        "git config --get user.email",
    ]


def test_fake_git_has_the_same_facade_surface() -> None:
    git = FakeGit()

    assert isinstance(git, Git)
    assert git.branches is git.branch_service
    assert git.worktrees is git.worktree_service
