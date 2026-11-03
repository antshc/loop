"""The agent client seam, the run scope several calls share, and the host."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from dishka import AsyncContainer, Scope

from .context import AgentCallContext, AgentRequest, AgentResult
from .errors import AgentReplyError, ReplyMismatch
from .middleware import Next


class AgentClient(Protocol):
    """Runs one rendered prompt on an agent CLI and returns its raw result."""

    async def run(self, prompt: str) -> AgentResult: ...


class AgentRun:
    """One Workflow run: its calls share SESSION-scope services such as caches."""

    def __init__(self, session: AsyncContainer, pipeline: Next) -> None:
        self._session = session
        self._pipeline = pipeline

    async def call[R](self, request: AgentRequest[R]) -> R:
        """Runs `request` in its own REQUEST scope; raises `AgentError` on failure."""
        async with self._session() as services:
            ctx = AgentCallContext(request, services, args=dict(request.args))
            try:
                await self._pipeline(ctx)
            except ReplyMismatch as exception:
                raise AgentReplyError(request.role, str(exception)) from exception
        if not isinstance(ctx.reply, request.reply_type):
            raise AgentReplyError(request.role, "pipeline produced no typed reply")
        return ctx.reply


class AgentHost:
    """Owns the APP-scope container and the built pipeline."""

    def __init__(self, container: AsyncContainer, pipeline: Next) -> None:
        self._container = container
        self._pipeline = pipeline

    @asynccontextmanager
    async def run_scope(self) -> AsyncIterator[AgentRun]:
        """Opens a SESSION scope whose services every call inside it shares."""
        async with self._container(scope=Scope.SESSION) as session:
            yield AgentRun(session, self._pipeline)

    async def run[R](self, request: AgentRequest[R]) -> R:
        """Runs one call in a run scope of its own."""
        async with self.run_scope() as run:
            return await run.call(request)

    async def aclose(self) -> None:
        """Closes the APP-scope container and its finalizers."""
        await self._container.close()
