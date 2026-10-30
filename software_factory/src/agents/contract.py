"""The typed agent every Agent role is: a workspace and a typed request in, a typed reply out."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from loop import AgentProfile, AgentRequest, AgentWorkspace


type Json = None | bool | int | float | str | list[Json] | dict[str, Json]


class Message:
    """Base of every request and reply: a frozen dataclass of plain values, nested messages and JSON values."""


@dataclass(frozen=True)
class Declined(Message):
    """The agent reported `failed` in its status envelope; the caller decides what declining means."""

    reason: str


class AgentError(Exception):
    """A role agent could not produce its reply; `role` names the agent."""

    def __init__(self, role: str, detail: str) -> None:
        super().__init__(f"{role} agent: {detail}")
        self.role = role
        self.detail = detail


class AgentRunError(AgentError):
    """The agent process failed, timed out, or its stream reported a non-zero exit code."""


class AgentReplyError(AgentError):
    """The agent's output has no envelope, or its envelope does not match the role's reply shape or echoed identifier."""


class ReplyMismatch(ValueError):
    """Raised by a role decoder when the output does not match the reply shape; `TypedAgent.run` reports it as an AgentReplyError."""


@dataclass(frozen=True)
class TypedAgent[Q: Message, R]:
    """One reusable role: its profile, how it encodes a request into a prompt, and how it decodes the output into a reply."""

    role: str
    profile: AgentProfile
    encode: Callable[[Q], str]
    decode: Callable[[Q, str], R]

    def run(self, workspace: AgentWorkspace, request: Q, *, session: str | None = None) -> R:
        """Runs the agent once in `workspace`; raises only AgentRunError or AgentReplyError."""
        prompt = self.encode(request)
        try:
            result = workspace.agent(self.profile).run(AgentRequest(prompt, session))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exception:
            raise AgentRunError(self.role, f"process failed: {exception}") from exception
        if result.exit_code:
            raise AgentRunError(self.role, f"exited with code {result.exit_code}")
        try:
            return self.decode(request, result.output)
        except ReplyMismatch as exception:
            raise AgentReplyError(self.role, str(exception)) from exception

    def tuned(self, **profile_fields: Any) -> TypedAgent[Q, R]:
        """A copy whose profile has `profile_fields` replaced, such as `timeout_sec`; this agent is unchanged."""
        return replace(self, profile=replace(self.profile, **profile_fields))
