"""The middleware contract: `RequestDelegate` and `IMiddleware` analogues."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from .context import AgentCallContext

type Next = Callable[[AgentCallContext], Awaitable[None]]


class Middleware(Protocol):
    """One link of the chain; it may act before and after `next`, or not call it."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None: ...


class Terminal(Protocol):
    """The end of the chain, the endpoint that runs the agent."""

    async def handle(self, ctx: AgentCallContext) -> None: ...
