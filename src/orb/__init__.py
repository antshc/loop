"""Library for composing agent workflows on capsules.

A workflow is an ordinary Python script that wires a capsule, an agent client, git and GitHub clients,
and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from orb.agents.copilot import CopilotClient, copilot
from orb.agents.dry_run import DryRunAgentClient, dry_run
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
from orb.contracts.capsule import AgentClientFactory, Capsule, CapsuleBinding
from orb.contracts.execution_store import ExecutionStore
from orb.errors import (
    AgentError,
    Cancelled,
    CommandError,
    ExecutionStoreError,
    ExtractionError,
    HookError,
    OrbError,
    PromptError,
)
from orb.logging_config import configure_logging
from orb.parallel import Settled, parallel_settled
from orb.platforms.gh_client import GitHubClient, PullRequest, ReviewThread, Spec, Ticket
from orb.platforms.git_client import GitClient, Hook, origin_slug, same_slug
from orb.process import CommandExecutor, CommandResult
from orb.prompt import PromptPreprocessor
from orb.sandbox.create_sandbox import CapsuleFactory, Sandbox, SandboxRunResult, create_sandbox
from orb.sandbox.sandbox_lifecycle import (
    LifecycleResult,
    SandboxHooks,
    run_host_hooks,
    with_sandbox_lifecycle,
)
from orb.stores.file import FileExecutionStore, FileSessionStore
from orb.stores.memory import InMemoryExecutionStore, InMemorySessionStore
from orb.tags import extract_json, extract_tag

__all__ = [
    "AgentClient",
    "AgentClientFactory",
    "AgentError",
    "AgentOptions",
    "AgentResult",
    "AgentSession",
    "Cancelled",
    "Capsule",
    "CapsuleBinding",
    "CapsuleFactory",
    "CommandError",
    "CommandExecutor",
    "CommandResult",
    "CopilotClient",
    "DEFAULT_COMPLETION_SIGNAL",
    "DockerCapsule",
    "DryRunAgentClient",
    "ExecutionStore",
    "ExecutionStoreError",
    "ExtractionError",
    "FileExecutionStore",
    "FileSessionStore",
    "GitClient",
    "GitHubClient",
    "Hook",
    "HookError",
    "InMemoryExecutionStore",
    "InMemorySessionStore",
    "LifecycleResult",
    "MAX_FAILED_ATTEMPTS",
    "Mount",
    "NoCapsule",
    "OrbError",
    "PromptError",
    "PromptPreprocessor",
    "PullRequest",
    "ReviewThread",
    "Sandbox",
    "SandboxHooks",
    "SandboxRunResult",
    "SessionStore",
    "Settled",
    "Spec",
    "Ticket",
    "configure_logging",
    "copilot",
    "create_sandbox",
    "dry_run",
    "extract_json",
    "extract_tag",
    "may_attempt",
    "origin_slug",
    "parallel_settled",
    "run_host_hooks",
    "same_slug",
    "with_sandbox_lifecycle",
]
