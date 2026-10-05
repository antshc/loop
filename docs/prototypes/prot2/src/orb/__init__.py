"""Library for composing agent workflows on git worktree capsules.

A workflow is an ordinary Python script that calls `run()` / `create_capsule()` with a
capsule provider and an agent, and owns its own control flow. Import only from here.
"""

from __future__ import annotations

from orb.agents.copilot import CopilotCliAgent, copilot
from orb.agents.scripted import ScriptedAgent
from orb.attempts import MAX_FAILED_ATTEMPTS, may_attempt
from orb.contracts.agent_client import AgentClient, AgentResult
from orb.contracts.execution_store import ExecutionStore
from orb.contracts.source_control_platform import PullRequest, ReviewThread, SourceControlPlatform
from orb.contracts.capsule import Hook, Hooks, CapsuleInstance, CapsuleProvider
from orb.contracts.work_tracker import WorkItem, WorkTracker
from orb.errors import AgentError, CommandError, ExtractionError, PromptError, OrbError
from orb.parallel import Settled, parallel_settled
from orb.platforms.factory import (
    source_control_for_repo,
    source_control_from_remote,
    work_tracker_for_repo,
    work_tracker_from_remote,
)
from orb.process import run_command
from orb.prompt import render_prompt
from orb.runner import run
from orb.capsule import DEFAULT_COMPLETION_SIGNAL, RunResult, Capsule, create_capsule
from orb.capsules.docker import DockerCapsuleProvider, Mount, docker
from orb.capsules.worktree import WorktreeCapsuleProvider, worktree
from orb.worktree import Worktree, create_worktree
from orb.stores.file import FileExecutionStore
from orb.tags import extract_json, extract_tag

__all__ = [
    "AgentClient",
    "AgentError",
    "AgentResult",
    "CommandError",
    "CopilotCliAgent",
    "DEFAULT_COMPLETION_SIGNAL",
    "DockerCapsuleProvider",
    "ExecutionStore",
    "ExtractionError",
    "FileExecutionStore",
    "Hook",
    "Hooks",
    "MAX_FAILED_ATTEMPTS",
    "Mount",
    "PromptError",
    "PullRequest",
    "ReviewThread",
    "RunResult",
    "Capsule",
    "CapsuleInstance",
    "CapsuleProvider",
    "ScriptedAgent",
    "SourceControlPlatform",
    "Settled",
    "OrbError",
    "WorkItem",
    "WorkTracker",
    "Worktree",
    "WorktreeCapsuleProvider",
    "copilot",
    "create_capsule",
    "docker",
    "create_worktree",
    "extract_json",
    "extract_tag",
    "may_attempt",
    "parallel_settled",
    "render_prompt",
    "run",
    "run_command",
    "source_control_for_repo",
    "source_control_from_remote",
    "work_tracker_for_repo",
    "work_tracker_from_remote",
    "worktree",
]
