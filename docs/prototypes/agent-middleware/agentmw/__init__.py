"""Agent middleware pipeline prototype: chain of responsibility around an agent client."""

from __future__ import annotations

from .composition import (
    bind,
    configure_pipeline,
    configure_role_pipelines,
    create_host,
)
from .context import AgentCallContext, AgentRequest, AgentResult
from .errors import (
    AgentDeclined,
    AgentError,
    AgentReplyError,
    AgentRunError,
    PromptError,
    ReplyMismatch,
    ReplyRejected,
)
from .host import AgentClient, AgentHost, AgentRun
from .integrations import (
    CheckResult,
    CheckRunner,
    Commit,
    GitHubClient,
    Issue,
    PlanStore,
    RetryPolicy,
    ReviewChecklist,
    TicketCache,
    WorktreeGit,
)
from .middleware import Middleware, Next, Terminal
from .pipeline import PipelineBuilder
from .terminal import AgentClientTerminal

__all__ = [
    "AgentCallContext",
    "AgentClient",
    "AgentClientTerminal",
    "AgentDeclined",
    "AgentError",
    "AgentHost",
    "AgentReplyError",
    "AgentRequest",
    "AgentResult",
    "AgentRun",
    "AgentRunError",
    "CheckResult",
    "CheckRunner",
    "Commit",
    "GitHubClient",
    "Issue",
    "Middleware",
    "Next",
    "PipelineBuilder",
    "PlanStore",
    "PromptError",
    "ReplyMismatch",
    "ReplyRejected",
    "RetryPolicy",
    "ReviewChecklist",
    "Terminal",
    "TicketCache",
    "WorktreeGit",
    "bind",
    "configure_pipeline",
    "configure_role_pipelines",
    "create_host",
]
