"""Re-runs the inner chain when the reply does not match its shape."""

from __future__ import annotations

from ..context import AgentCallContext
from ..errors import AgentReplyError, ReplyMismatch
from ..integrations import RetryPolicy
from ..middleware import Next


class ReplyRetryMiddleware:
    """Calls `next` again with the mismatch as feedback, up to the policy's attempts."""

    def __init__(self, policy: RetryPolicy) -> None:
        self._policy = policy

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Retries only `ReplyMismatch`; run errors propagate at once."""
        while True:
            try:
                await next(ctx)
                return
            except ReplyMismatch as exception:
                if ctx.attempt >= self._policy.max_attempts:
                    raise AgentReplyError(
                        ctx.role, f"{exception} after {ctx.attempt} attempt(s)"
                    ) from exception
                ctx.feedback = str(exception)
                ctx.attempt += 1
                ctx.result = None
                ctx.payload = None
                ctx.reply = None
