"""Fills `{{CHECKLIST}}`; registered for the reviewer role only."""

from __future__ import annotations

from ..context import AgentCallContext
from ..integrations import ReviewChecklist
from ..middleware import Next


class ReviewChecklistMiddleware:
    """Pre-agent: renders the configured checklist as a Markdown list."""

    def __init__(self, checklist: ReviewChecklist) -> None:
        self._checklist = checklist

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Sets CHECKLIST."""
        ctx.args["CHECKLIST"] = "\n".join(f"- {item}" for item in self._checklist.items)
        await next(ctx)
