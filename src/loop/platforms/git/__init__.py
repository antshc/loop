"""Git access: self-locating `Branch`/`Worktree`/`Commit` objects over the internal `GitClient` command runner."""

from __future__ import annotations

from loop.platforms.git.branch_service import BranchService
from loop.platforms.git.client import GitClient, Hook
from loop.platforms.git.commit_service import CommitService
from loop.platforms.git.facade import Git
from loop.platforms.git.objects import Branch, Commit, RepositoryData, Worktree
from loop.platforms.git.worktree_service import WorktreeService

__all__ = [
    "Branch",
    "BranchService",
    "Commit",
    "CommitService",
    "Git",
    "GitClient",
    "Hook",
    "RepositoryData",
    "WorktreeService",
    "Worktree",
]
