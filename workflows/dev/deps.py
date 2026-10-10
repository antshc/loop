"""Dependencies `main` wires once and passes through the orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loop import AgentBuilder, LoopHook
from workflows.platforms.git import WorkflowGit
from workflows.platforms.work_tracking import GitHubClient, RepositoryPool, TicketsTracker

from .store import FileExecutionStore

GithubFactory = Callable[[Path], GitHubClient]


@dataclass(frozen=True)
class Prompts:
    dev: str


@dataclass(frozen=True)
class DevDeps:
    repository_pool: RepositoryPool
    tracker: TicketsTracker
    store: FileExecutionStore
    git: WorkflowGit
    new_agent: Callable[[], AgentBuilder]
    loop_hooks: tuple[LoopHook, ...]
    prompts: Prompts
