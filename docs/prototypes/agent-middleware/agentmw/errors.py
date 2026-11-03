"""Failures an agent call can end with."""

from __future__ import annotations


class PromptError(ValueError):
    """A prompt template placeholder has no argument after every middleware ran."""


class ReplyMismatch(ValueError):
    """The agent output does not match the reply shape; retryable."""


class AgentError(Exception):
    """An agent call could not produce its reply; `role` names the agent."""

    def __init__(self, role: str, detail: str) -> None:
        super().__init__(f"{role} agent: {detail}")
        self.role = role
        self.detail = detail


class AgentRunError(AgentError):
    """The agent process failed or exited with a non-zero code; not retried."""


class AgentReplyError(AgentError):
    """The agent output never matched the reply shape."""
