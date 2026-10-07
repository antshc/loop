"""Dependencies `main` wires once and passes through the orchestration."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loop import AgentClientFactory, GitClient, Hook, SandboxFactory
from workflows.platforms.work_tracking import GitHubClient, TicketsTracker

GithubFactory = Callable[[Path], GitHubClient]


@dataclass(frozen=True)
class DevDeps:
    harness_root: Path
    harness_slug: str
    tracker: TicketsTracker
    github_factory: GithubFactory
    git: GitClient
    agent_factory: AgentClientFactory
    sandbox_factory: SandboxFactory
    hooks: Sequence[Hook]
    template: str
    cancel: threading.Event
