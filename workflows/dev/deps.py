"""Dependencies `main` wires once and passes through the orchestration."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loop import AgentClientFactory, Git, Hook, SandboxFactory
from workflows.platforms.work_tracking import GitHubClient, RepositoryPool, TicketsTracker

GithubFactory = Callable[[Path], GitHubClient]


@dataclass(frozen=True)
class DevDeps:
    harness_root: Path
    repository_pool: RepositoryPool
    tracker: TicketsTracker
    git: Git
    agent_factory: AgentClientFactory
    sandbox_factory: SandboxFactory
    hooks: Sequence[Hook]
    template: str
    cancel: threading.Event
