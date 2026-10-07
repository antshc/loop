from __future__ import annotations

from loop.platforms.git.branch_service import BranchService
from loop.platforms.git.client import GitClient, GitRunner
from loop.platforms.git.commit_service import CommitService
from loop.platforms.git.worktree_service import WorktreeService
from loop.process import execute


class Git:
    """Git subsystem grouped by domain services over one shared command runner."""

    def __init__(self, *, run: GitRunner = execute) -> None:
        client = GitClient(run=run)
        self.branches = BranchService(client, run=run)
        self.worktrees = WorktreeService(client, run=run)
        self.commits = CommitService(run=run)
