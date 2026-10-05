"""Library for composing agent workflows on git worktree sandboxes.

A workflow is an ordinary Python script that calls `run()` / `create_sandbox()` with a
sandbox provider and an agent, and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from orb.agents.copilot import CopilotCliAgent, copilot
from orb.agents.scripted import ScriptedAgent
from orb.attempts import MAX_FAILED_ATTEMPTS, may_attempt
from orb.contracts.agent_client import AgentClient, AgentResult
from orb.contracts.execution_store import ExecutionStore
from orb.contracts.platform_adapter import PlatformAdapter, PullRequest, ReviewThread, WorkItem
from orb.contracts.sandbox import Hook, Hooks, SandboxInstance, SandboxProvider
from orb.errors import AgentError, CommandError, ExtractionError, PromptError, OrbError
from orb.parallel import Settled, parallel_settled
from orb.platforms.factory import platform_for_repo, platform_from_remote
from orb.process import run_command
from orb.prompt import render_prompt
from orb.runner import run
from orb.sandbox import DEFAULT_COMPLETION_SIGNAL, RunResult, Sandbox, create_sandbox
from orb.sandboxes.worktree import WorktreeSandboxProvider, worktree
from orb.stores.file import FileExecutionStore
from orb.tags import extract_json, extract_tag

__all__ = [
    "AgentClient",
    "AgentError",
    "AgentResult",
    "CommandError",
    "CopilotCliAgent",
    "DEFAULT_COMPLETION_SIGNAL",
    "ExecutionStore",
    "ExtractionError",
    "FileExecutionStore",
    "Hook",
    "Hooks",
    "MAX_FAILED_ATTEMPTS",
    "PlatformAdapter",
    "PromptError",
    "PullRequest",
    "ReviewThread",
    "RunResult",
    "Sandbox",
    "SandboxInstance",
    "SandboxProvider",
    "ScriptedAgent",
    "Settled",
    "OrbError",
    "WorkItem",
    "WorktreeSandboxProvider",
    "copilot",
    "create_sandbox",
    "extract_json",
    "extract_tag",
    "may_attempt",
    "parallel_settled",
    "platform_for_repo",
    "platform_from_remote",
    "render_prompt",
    "run",
    "run_command",
    "worktree",
]
