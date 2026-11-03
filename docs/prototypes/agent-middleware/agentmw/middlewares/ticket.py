"""Reads the GitHub issue named in the request and fills `{{TICKET}}`."""

from __future__ import annotations

import re

from ..context import AgentCallContext
from ..integrations import Issue, TicketCache
from ..middleware import Next

_ISSUE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)#(?P<number>\d+)$")


class GitHubTicketMiddleware:
    """Pre-agent: turns `metadata["issue"]` (`owner/repo#12`) into the TICKET argument."""

    def __init__(self, tickets: TicketCache) -> None:
        self._tickets = tickets

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills TICKET when the request names an issue; otherwise passes through."""
        reference = ctx.request.metadata.get("issue")
        if reference is not None:
            ctx.args["TICKET"] = _markdown(await self._fetch(reference))
        await next(ctx)

    async def _fetch(self, reference: str) -> Issue:
        match = _ISSUE.match(reference)
        if match is None:
            raise ValueError(f"issue reference must be owner/repo#N: {reference}")
        return await self._tickets.get_issue(match["repo"], int(match["number"]))


def _markdown(issue: Issue) -> str:
    return f"### {issue.repo}#{issue.number}: {issue.title}\n\n{issue.body}"
