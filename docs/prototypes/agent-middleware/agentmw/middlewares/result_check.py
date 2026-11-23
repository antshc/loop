"""Fails the call when the agent process did not succeed."""

from __future__ import annotations

from ..context import AgentCallContext
from ..errors import AgentRunError
from ..middleware import Next


class ResultCheckMiddleware:
    """Post-agent: a non-zero exit raises before any parser sees the output."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Checks `ctx.result` after the terminal ran."""
        await next(ctx)
        if ctx.result is None:
            raise AgentRunError(ctx.role, "terminal produced no result")
        if ctx.result.exit_code:
            raise AgentRunError(ctx.role, f"exited with code {ctx.result.exit_code}")
