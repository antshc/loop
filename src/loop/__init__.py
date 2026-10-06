"""Library for composing agent workflows on sandboxes.

A workflow is an ordinary Python script that wires a sandbox, an agent client, git and GitHub clients,
and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from loop.agents.copilot import CopilotClient, copilot
from loop.agents.dry_run import DryRunAgentClient, dry_run
from loop.attempts import MAX_FAILED_ATTEMPTS, may_attempt
from loop.contracts.agent_client import (
    DEFAULT_COMPLETION_SIGNAL,
    AgentClient,
    AgentOptions,
    AgentResult,
    AgentSession,
    SessionStore,
)
from loop.contracts.execution_store import ExecutionStore
from loop.contracts.sandbox import AgentClientFactory, Sandbox, SandboxBinding
from loop.errors import (
    AgentError,
    Cancelled,
    CommandError,
    ExecutionStoreError,
    ExtractionError,
    HookError,
    LoopError,
    PromptError,
)
from loop.logging_config import configure_logging
from loop.parallel import Settled, parallel_settled
from loop.platforms.gh_client import GitHubClient, PullRequest, ReviewThread, Spec, Ticket
from loop.platforms.git_client import GitClient, Hook, origin_slug, same_slug
from loop.process import CommandExecutor, CommandResult
from loop.prompt import PromptPreprocessor
from loop.sandbox.create_sandbox import SandboxFactory, SandboxRunResult, WorktreeSandbox, create_sandbox
from loop.sandbox.sandbox_lifecycle import (
    LifecycleResult,
    SandboxHooks,
    run_host_hooks,
    with_sandbox_lifecycle,
)
from loop.sandboxes.docker import DockerSandbox, Mount
from loop.sandboxes.no_sandbox import NoSandbox
from loop.stores.file import FileExecutionStore, FileSessionStore
from loop.stores.memory import InMemoryExecutionStore, InMemorySessionStore
from loop.tags import extract_json, extract_tag

__all__ = [
    "DEFAULT_COMPLETION_SIGNAL",
    "MAX_FAILED_ATTEMPTS",
    "AgentClient",
    "AgentClientFactory",
    "AgentError",
    "AgentOptions",
    "AgentResult",
    "AgentSession",
    "Cancelled",
    "CommandError",
    "CommandExecutor",
    "CommandResult",
    "CopilotClient",
    "DockerSandbox",
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
    "LoopError",
    "Mount",
    "NoSandbox",
    "PromptError",
    "PromptPreprocessor",
    "PullRequest",
    "ReviewThread",
    "Sandbox",
    "SandboxBinding",
    "SandboxFactory",
    "SandboxHooks",
    "SandboxRunResult",
    "SessionStore",
    "Settled",
    "Spec",
    "Ticket",
    "WorktreeSandbox",
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
