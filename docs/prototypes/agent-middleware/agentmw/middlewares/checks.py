"""Runs the verification commands after the implementer edited the worktree."""

from __future__ import annotations

from ..context import AgentCallContext
from ..errors import ReplyMismatch
from ..integrations import CheckRunner
from ..middleware import Next


class VerifyChecksMiddleware:
    """Implementer, post-agent: failing checks become feedback for a retry."""

    def __init__(self, checks: CheckRunner) -> None:
        self._checks = checks

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Raises `ReplyMismatch` with the check output so the retry re-asks."""
        await next(ctx)
        result = await self._checks.run()
        if not result.passed:
            raise ReplyMismatch(f"verification checks failed:\n{result.output}")
