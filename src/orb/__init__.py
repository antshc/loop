"""Library for composing agent workflows on capsules.

A workflow is an ordinary Python script that wires a capsule, an agent client, git and GitHub clients,
and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from orb.agents.copilot import CopilotClient, copilot
from orb.attempts import MAX_FAILED_ATTEMPTS, may_attempt
from orb.capsules.docker import DockerCapsule, Mount
from orb.capsules.no_capsule import NoCapsule
from orb.contracts.agent_client import (
    DEFAULT_COMPLETION_SIGNAL,
    AgentClient,
    AgentOptions,
    AgentResult,
    AgentSession,
    SessionStore,
)
from orb.contracts.capsule import AgentClientFactory, Capsule
from orb.contracts.execution_store import ExecutionStore
from orb.errors import AgentError, CommandError, ExtractionError, OrbError, PromptError
from orb.parallel import Settled, parallel_settled
from orb.platforms.gh_client import GitHubClient, PullRequest, ReviewThread, WorkItem
from orb.platforms.git_client import GitClient
from orb.process import CommandExecutor, CommandResult
from orb.prompt import PromptPreprocessor
from orb.stores.file import FileExecutionStore, FileSessionStore
from orb.stores.memory import InMemorySessionStore
from orb.tags import extract_json, extract_tag

__all__ = [
    "AgentClient",
    "AgentClientFactory",
    "AgentError",
    "AgentOptions",
    "AgentResult",
    "AgentSession",
    "Capsule",
    "CommandError",
    "CommandExecutor",
    "CommandResult",
    "CopilotClient",
    "DEFAULT_COMPLETION_SIGNAL",
    "DockerCapsule",
    "ExecutionStore",
    "ExtractionError",
    "FileExecutionStore",
    "FileSessionStore",
    "GitClient",
    "GitHubClient",
    "InMemorySessionStore",
    "MAX_FAILED_ATTEMPTS",
    "Mount",
    "NoCapsule",
    "OrbError",
    "PromptError",
    "PromptPreprocessor",
    "PullRequest",
    "ReviewThread",
    "SessionStore",
    "Settled",
    "WorkItem",
    "copilot",
    "extract_json",
    "extract_tag",
    "may_attempt",
    "parallel_settled",
]
