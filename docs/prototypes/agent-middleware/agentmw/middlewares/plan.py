"""Hands the planner's reply to the implementer through the run's `PlanStore`."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass

from ..context import AgentCallContext
from ..errors import PromptError
from ..integrations import PlanStore
from ..middleware import Next


class RememberPlanMiddleware:
    """Planner, post-agent: stores the typed reply once it has been decoded."""

    def __init__(self, store: PlanStore) -> None:
        self._store = store

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Saves `ctx.reply` as JSON after the inner chain succeeded."""
        await next(ctx)
        reply = asdict(ctx.reply) if is_dataclass(ctx.reply) else ctx.reply
        self._store.plan = json.dumps(reply, indent=2)


class PlanMiddleware:
    """Implementer, pre-agent: fills PLAN from the store unless the caller passed it."""

    def __init__(self, store: PlanStore) -> None:
        self._store = store

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Sets PLAN; raises `PromptError` when no planner ran in this run."""
        if "PLAN" not in ctx.args:
            if self._store.plan is None:
                raise PromptError("no plan in this run; run the planner first")
            ctx.args["PLAN"] = self._store.plan
        await next(ctx)
