"""Library for composing agent workflows on git worktrees.

A workflow is an ordinary Python script that wires a worktree runner, an agent client, git and GitHub clients,
and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from loop.agents.copilot import CopilotClient, CopilotOutputParser, copilot
from loop.contracts.agent_client import (
    AgentBinding,
    AgentClient,
    AgentClientFactory,
    AgentOptions,
    AgentOutputParser,
    AgentResult,
    AgentSession,
    SessionStore,
)
from loop.contracts.execution_store import ExecutionStore
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
from loop.platforms.git import (
    Branch,
    BranchService,
    Commit,
    CommitService,
    Git,
    Hook,
    Worktree,
    WorktreeService,
)
from loop.process import CommandExecutor, CommandResult, cli_runner, run_command
from loop.prompt import PromptPreprocessor
from loop.runs.lifecycle import WorktreeRunner, WorktreeRunResult, create_worktree_runner, run_host_hooks
from loop.stores.file import FileExecutionStore, FileSessionStore
from loop.stores.memory import InMemoryExecutionStore, InMemorySessionStore
from loop.tags import extract_json, extract_tag

__all__ = [
    "AgentBinding",
    "AgentClient",
    "AgentClientFactory",
    "AgentError",
    "AgentOptions",
    "AgentOutputParser",
    "AgentResult",
    "AgentSession",
    "Branch",
    "BranchService",
    "Cancelled",
    "CommandError",
    "CommandExecutor",
    "CommandResult",
    "Commit",
    "CommitService",
    "CopilotClient",
    "CopilotOutputParser",
    "ExecutionStore",
    "ExecutionStoreError",
    "ExtractionError",
    "FileExecutionStore",
    "FileSessionStore",
    "Git",
    "Hook",
    "HookError",
    "InMemoryExecutionStore",
    "InMemorySessionStore",
    "LoopError",
    "PromptError",
    "PromptPreprocessor",
    "SessionStore",
    "Settled",
    "Worktree",
    "WorktreeRunResult",
    "WorktreeRunner",
    "WorktreeService",
    "cli_runner",
    "configure_logging",
    "copilot",
    "create_worktree_runner",
    "extract_json",
    "extract_tag",
    "parallel_settled",
    "run_command",
    "run_host_hooks",
]
