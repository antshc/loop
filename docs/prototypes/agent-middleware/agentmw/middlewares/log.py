"""Logs each call's role, attempts, outcome and duration."""

from __future__ import annotations

import logging
import time

from ..context import AgentCallContext
from ..middleware import Next

logger = logging.getLogger("agentmw")


class LoggingMiddleware:
    """Outermost link, so its timing covers every other middleware."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Logs before and after the rest of the chain."""
        started = time.perf_counter()
        logger.info("%s: started", ctx.role)
        try:
            await next(ctx)
        except Exception as exception:
            logger.warning(
                "%s: failed after %d attempt(s) in %.3fs: %s",
                ctx.role,
                ctx.attempt,
                time.perf_counter() - started,
                exception,
            )
            raise
        logger.info(
            "%s: done after %d attempt(s) in %.3fs",
            ctx.role,
            ctx.attempt,
            time.perf_counter() - started,
        )
