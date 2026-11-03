"""Demo: planner → implementer → reviewer in one run, each with its own middlewares."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from . import (
    AgentClient,
    AgentRequest,
    AgentResult,
    CheckResult,
    CheckRunner,
    GitHubClient,
    Issue,
)
from .composition import bind, create_host
from .fakes import FakeGitHubClient, ScriptedAgentClient, ScriptedChecks

TICKET = "antshc/loop#12"

PLAN_TEMPLATE = """\
You are the planner. Do not edit files.

## Ticket
{{TICKET}}

## Response format
{{RESPONSE_FORMAT}}
"""

IMPLEMENT_TEMPLATE = """\
You are the implementer. Follow the plan.

## Ticket
{{TICKET}}

## Plan
{{PLAN}}

## Response format
{{RESPONSE_FORMAT}}
"""

REVIEW_TEMPLATE = """\
You are the reviewer. Review the change made for this ticket.

## Ticket
{{TICKET}}

## Checklist
{{CHECKLIST}}

## Response format
{{RESPONSE_FORMAT}}
"""


@dataclass(frozen=True)
class PlanReply:
    status: str
    summary: str
    steps: list[str]


@dataclass(frozen=True)
class ImplementReply:
    status: str
    changed_files: list[str]


@dataclass(frozen=True)
class ReviewReply:
    approved: bool
    comments: list[str]


def scripted_agent() -> ScriptedAgentClient:
    plan = {"status": "ok", "summary": "Add a cache", "steps": ["Add", "Test"]}
    implemented = json.dumps({"status": "ok", "changed_files": ["cache.py"]})
    return ScriptedAgentClient(
        AgentResult("I think we should add a cache.", 0),
        AgentResult(f"Plan ready.\n{json.dumps(plan)}", 0),
        AgentResult(implemented, 0),
        AgentResult(implemented, 0),
        AgentResult(json.dumps({"approved": True, "comments": []}), 0),
    )


async def main() -> None:
    """Runs the three calls against fakes and prints each prompt and typed reply."""
    agent = scripted_agent()
    github = FakeGitHubClient(
        [Issue("antshc/loop", 12, "Cache lookups", "Lookups are slow; cache them.")]
    )
    checks = ScriptedChecks(CheckResult(False, "FAILED test_cache.py::test_hit"))
    host = create_host(
        bind(GitHubClient, github),
        bind(AgentClient, agent),
        bind(CheckRunner, checks),
    )
    try:
        async with host.run_scope() as run:
            plan = await run.call(
                AgentRequest(
                    "planner", PLAN_TEMPLATE, PlanReply, metadata={"issue": TICKET}
                )
            )
            # No PLAN argument: PlanMiddleware reads what RememberPlanMiddleware stored.
            change = await run.call(
                AgentRequest(
                    "implementer",
                    IMPLEMENT_TEMPLATE,
                    ImplementReply,
                    metadata={"issue": TICKET},
                )
            )
            review = await run.call(
                AgentRequest(
                    "reviewer", REVIEW_TEMPLATE, ReviewReply, metadata={"issue": TICKET}
                )
            )
    finally:
        await host.aclose()
    for index, prompt in enumerate(agent.prompts, start=1):
        print(f"===== prompt {index} =====\n{prompt}\n")
    print(f"planner reply: {plan}")
    print(f"implementer reply: {change}")
    print(f"reviewer reply: {review}")
    print(f"GitHub fetches: {github.fetches}, check runs: {checks.runs}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    asyncio.run(main())
