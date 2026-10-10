"""Standalone contract prototype: Agent builder with composed execution wrappers.

No real Git worktree, Docker container, or Copilot process is started by default.
"""

from __future__ import annotations

import logging

from .agents import AgentClient, AgentWrapper, CliAgentClient, DockerAgent, GitAgent
from .builder import AgentBuilder, Worktree
from .clis import (
    DEFAULT,
    AgentCli,
    AgentProfile,
    CliRunner,
    CodexCli,
    CopilotCli,
    ProcessCliRunner,
    codex,
    copilot,
)
from .docker import DockerRuntime, DockerService
from .dryrun import LoggingDocker, LoggingGitCli, LoggingRunner
from .factory import Agent, repo_agent
from .git import (
    BranchStrategy,
    GitCli,
    GitOptions,
    GitRuntime,
    GitService,
    GitStrategy,
    HeadStrategy,
    MergeToHeadStrategy,
)
from .hooks import (
    AgentCliHook,
    AgentCliHookPoint,
    AgentCliHookWiring,
    AgentStopAgentCliHook,
    LoopHook,
    LoopHookError,
    LoopHookPoint,
    PostToolAgentCliHook,
    PreCompactAgentCliHook,
    PreToolAgentCliHook,
    PromptSubmittedAgentCliHook,
    RunFinishedLoopHook,
    SessionEndAgentCliHook,
    SessionStartAgentCliHook,
    SubagentStartAgentCliHook,
    SubagentStopAgentCliHook,
    UnsupportedAgentCliHookPoint,
    WorktreeReadyLoopHook,
    WorktreeRemovingLoopHook,
)
from .log import configure_logging
from .run import AgentContext, AgentOptions, AgentRequest, AgentResult, RunContext
from .sessions import (
    CliOutcome,
    MemorySessionStore,
    NativeHandle,
    Resume,
    SessionCliMismatch,
    SessionHandleMissing,
    SessionName,
    SessionStore,
    Start,
    Turn,
)

# Silent until the host calls configure_logging() or sets up its own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "DEFAULT",
    "Agent",
    "AgentBuilder",
    "AgentCli",
    "AgentCliHook",
    "AgentCliHookPoint",
    "AgentCliHookWiring",
    "AgentClient",
    "AgentContext",
    "AgentOptions",
    "AgentProfile",
    "AgentRequest",
    "AgentResult",
    "AgentStopAgentCliHook",
    "AgentWrapper",
    "BranchStrategy",
    "CliAgentClient",
    "CliOutcome",
    "CliRunner",
    "CodexCli",
    "CopilotCli",
    "DockerAgent",
    "DockerRuntime",
    "DockerService",
    "GitAgent",
    "GitCli",
    "GitOptions",
    "GitRuntime",
    "GitService",
    "GitStrategy",
    "HeadStrategy",
    "LoggingDocker",
    "LoggingGitCli",
    "LoggingRunner",
    "LoopHook",
    "LoopHookError",
    "LoopHookPoint",
    "MemorySessionStore",
    "MergeToHeadStrategy",
    "NativeHandle",
    "PostToolAgentCliHook",
    "PreCompactAgentCliHook",
    "PreToolAgentCliHook",
    "ProcessCliRunner",
    "PromptSubmittedAgentCliHook",
    "Resume",
    "RunContext",
    "RunFinishedLoopHook",
    "SessionCliMismatch",
    "SessionEndAgentCliHook",
    "SessionHandleMissing",
    "SessionName",
    "SessionStartAgentCliHook",
    "SessionStore",
    "Start",
    "SubagentStartAgentCliHook",
    "SubagentStopAgentCliHook",
    "Turn",
    "UnsupportedAgentCliHookPoint",
    "Worktree",
    "WorktreeReadyLoopHook",
    "WorktreeRemovingLoopHook",
    "codex",
    "configure_logging",
    "copilot",
    "repo_agent",
]
