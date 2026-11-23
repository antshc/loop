"""The endpoint: renders the filled template and calls the agent client."""

from __future__ import annotations

from .context import AgentCallContext
from .host import AgentClient
from .template import render


class AgentClientTerminal:
    """Renders `ctx.args` into the template, appends retry feedback, and runs the agent."""

    def __init__(self, client: AgentClient) -> None:
        self._client = client

    async def handle(self, ctx: AgentCallContext) -> None:
        """Sets `ctx.prompt` and `ctx.result`."""
        prompt = render(ctx.request.template, ctx.args)
        if ctx.feedback is not None:
            prompt += f"\n\n## Your previous reply was rejected\n{ctx.feedback}"
        ctx.prompt = prompt
        ctx.result = await self._client.run(prompt)


class UnknownRoleTerminal:
    """The fallback of per-role chains: a role with no chain is a wiring error."""

    async def handle(self, ctx: AgentCallContext) -> None:
        """Raises `LookupError` naming the unmapped role."""
        raise LookupError(f"no pipeline is mapped for role {ctx.role!r}")
