"""Library for composing agent workflows on sandboxes.

A workflow is an ordinary Python script that wires a sandbox, an agent client, git and GitHub clients,
and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from loop.agents.copilot import CopilotClient, CopilotOutputParser, copilot
from loop.contracts.agent_client import (
    AgentClient,
    AgentOptions,
    AgentOutputParser,
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
from loop.platforms.gh_client import Comment, GitHubClient, PullRequest, ReviewThread, Spec, Ticket
from loop.platforms.git_client import Branch, Commit, GitClient, Hook, origin_slug, same_slug, slugify
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
    "AgentClient",
    "AgentClientFactory",
    "AgentError",
    "AgentOptions",
    "AgentOutputParser",
    "AgentResult",
    "AgentSession",
    "Branch",
    "Cancelled",
    "CommandError",
    "CommandExecutor",
    "CommandResult",
    "Comment",
    "Commit",
    "CopilotClient",
    "CopilotOutputParser",
    "DockerSandbox",
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
    "extract_json",
    "extract_tag",
    "origin_slug",
    "parallel_settled",
    "run_host_hooks",
    "same_slug",
    "slugify",
    "with_sandbox_lifecycle",
]
