"""The request, the raw result, and the per-call context the pipeline flows through."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dishka import AsyncContainer


@dataclass(frozen=True)
class AgentRequest[R]:
    """What a caller asks for: a role, a `{{KEY}}` template, and the reply type."""

    role: str
    template: str
    reply_type: type[R]
    args: Mapping[str, str] = field(default_factory=dict)
    # Inputs for middlewares, such as `issue="owner/repo#12"`; never rendered directly.
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    """The raw outcome of one agent process run."""

    output: str
    exit_code: int


@dataclass
class AgentCallContext:
    """Mutable state of one agent call, the `HttpContext` of the pipeline."""

    request: AgentRequest[Any]
    # The REQUEST-scope container, like `HttpContext.RequestServices`.
    services: AsyncContainer
    args: dict[str, str] = field(default_factory=dict)
    attempt: int = 1
    feedback: str | None = None
    prompt: str | None = None
    result: AgentResult | None = None
    # The JSON object to decode, when a middleware unwrapped it from an envelope.
    payload: dict[str, Any] | None = None
    reply: Any = None
    items: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str:
        """The role of the request."""
        return self.request.role
