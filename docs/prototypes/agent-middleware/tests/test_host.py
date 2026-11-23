from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from agentmw import (
    AgentClient,
    AgentHost,
    AgentReplyError,
    AgentRequest,
    AgentResult,
    AgentRunError,
    CheckResult,
    CheckRunner,
    GitHubClient,
    Issue,
    PromptError,
    RetryPolicy,
    bind,
    configure_pipeline,
    configure_role_pipelines,
    create_host,
)
from agentmw.fakes import FakeGitHubClient, ScriptedAgentClient, ScriptedChecks

TEMPLATE = "{{TICKET}}\n{{RESPONSE_FORMAT}}"


@dataclass(frozen=True)
class Plan:
    summary: str


def request(role: str = "planner", template: str = TEMPLATE) -> AgentRequest[Plan]:
    return AgentRequest(role, template, Plan, metadata={"issue": "o/r#1"})


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, agent: ScriptedAgentClient, *extra, req=None) -> Plan:
        github = FakeGitHubClient([Issue("o/r", 1, "Do it", "Details")])
        host = create_host(bind(GitHubClient, github), bind(AgentClient, agent), *extra)
        try:
            return await host.run(req or request())
        finally:
            await host.aclose()

    async def test_returns_the_typed_reply_from_a_filled_prompt(self) -> None:
        agent = ScriptedAgentClient(AgentResult(json.dumps({"summary": "s"}), 0))

        reply = await self.run_with(agent)

        self.assertEqual(Plan("s"), reply)
        self.assertIn("o/r#1: Do it", agent.prompts[0])
        self.assertIn('"summary": "str"', agent.prompts[0])

    async def test_retries_a_mismatched_reply(self) -> None:
        agent = ScriptedAgentClient(
            AgentResult("no json", 0), AgentResult('{"summary": "s"}', 0)
        )

        reply = await self.run_with(agent)

        self.assertEqual(Plan("s"), reply)
        self.assertIn("previous reply was rejected", agent.prompts[1])

    async def test_a_caller_provider_overrides_the_retry_policy(self) -> None:
        agent = ScriptedAgentClient(AgentResult("no json", 0))

        with self.assertRaises(AgentReplyError):
            await self.run_with(agent, bind(RetryPolicy, RetryPolicy(1)))
        self.assertEqual(1, len(agent.prompts))

    async def test_a_failed_run_is_not_retried(self) -> None:
        agent = ScriptedAgentClient(AgentResult("crash", 1))

        with self.assertRaises(AgentRunError):
            await self.run_with(agent)

    async def test_reviewer_only_placeholder_is_unfilled_for_other_roles(
        self,
    ) -> None:
        template = TEMPLATE + "\n{{CHECKLIST}}"
        reviewer = ScriptedAgentClient(AgentResult('{"summary": "s"}', 0))

        await self.run_with(reviewer, req=request("reviewer", template))

        self.assertIn("- Tests cover the change", reviewer.prompts[0])
        with self.assertRaises(PromptError):
            await self.run_with(ScriptedAgentClient(), req=request("planner", template))


@dataclass(frozen=True)
class Implemented:
    changed: list[str]


class RunScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.github = FakeGitHubClient([Issue("o/r", 1, "Do it", "Details")])
        self.agent = ScriptedAgentClient(
            AgentResult('{"summary": "add cache"}', 0),
            AgentResult('{"changed": ["cache.py"]}', 0),
        )
        self.host = create_host(
            bind(GitHubClient, self.github), bind(AgentClient, self.agent)
        )

    async def asyncTearDown(self) -> None:
        await self.host.aclose()

    def implement(self, plan: Plan) -> AgentRequest[Implemented]:
        return AgentRequest(
            "implementer",
            "{{TICKET}}\n## Plan\n{{PLAN}}\n{{RESPONSE_FORMAT}}",
            Implemented,
            args={"PLAN": plan.summary},
            metadata={"issue": "o/r#1"},
        )

    async def test_planner_reply_feeds_the_implementer_in_one_run(self) -> None:
        async with self.host.run_scope() as run:
            plan = await run.call(request())
            done = await run.call(self.implement(plan))

        self.assertEqual(Implemented(["cache.py"]), done)
        self.assertIn("## Plan\nadd cache", self.agent.prompts[1])
        self.assertEqual(1, self.github.fetches)

    async def test_separate_runs_do_not_share_the_ticket_cache(self) -> None:
        plan = await self.host.run(request())
        await self.host.run(self.implement(plan))

        self.assertEqual(2, self.github.fetches)

    async def test_an_implementer_failure_does_not_rerun_the_planner(self) -> None:
        agent = ScriptedAgentClient(
            AgentResult('{"summary": "s"}', 0), AgentResult("crash", 1)
        )
        host = create_host(bind(GitHubClient, self.github), bind(AgentClient, agent))
        try:
            async with host.run_scope() as run:
                plan = await run.call(request())
                with self.assertRaises(AgentRunError):
                    await run.call(self.implement(plan))
        finally:
            await host.aclose()
        self.assertEqual(2, len(agent.prompts))


class RoleMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.github = FakeGitHubClient([Issue("o/r", 1, "Do it", "Details")])

    def host(self, agent: ScriptedAgentClient, checks: ScriptedChecks) -> AgentHost:
        return create_host(
            bind(GitHubClient, self.github),
            bind(AgentClient, agent),
            bind(CheckRunner, checks),
            configure=self.configure,
        )

    configure = staticmethod(configure_pipeline)

    def implement(self) -> AgentRequest[Implemented]:
        return AgentRequest(
            "implementer",
            "## Plan\n{{PLAN}}\n{{RESPONSE_FORMAT}}",
            Implemented,
            metadata={"issue": "o/r#1"},
        )

    async def test_implementer_reads_the_plan_the_planner_stored(self) -> None:
        agent = ScriptedAgentClient(
            AgentResult('{"summary": "add cache"}', 0),
            AgentResult('{"changed": ["cache.py"]}', 0),
        )
        host = self.host(agent, ScriptedChecks())
        try:
            async with host.run_scope() as run:
                await run.call(request())
                await run.call(self.implement())
        finally:
            await host.aclose()

        self.assertIn('"summary": "add cache"', agent.prompts[1])

    async def test_implementer_without_a_planner_in_the_run_fails(self) -> None:
        host = self.host(ScriptedAgentClient(), ScriptedChecks())
        try:
            with self.assertRaisesRegex(PromptError, "run the planner first"):
                await host.run(self.implement())
        finally:
            await host.aclose()

    async def test_failing_checks_re_ask_only_the_implementer(self) -> None:
        agent = ScriptedAgentClient(
            AgentResult('{"summary": "s"}', 0),
            AgentResult('{"changed": []}', 0),
            AgentResult('{"changed": ["cache.py"]}', 0),
        )
        checks = ScriptedChecks(CheckResult(False, "FAILED test_hit"))
        host = self.host(agent, checks)
        try:
            async with host.run_scope() as run:
                await run.call(request())
                done = await run.call(self.implement())
        finally:
            await host.aclose()

        self.assertEqual(Implemented(["cache.py"]), done)
        self.assertEqual(2, checks.runs)
        self.assertIn("FAILED test_hit", agent.prompts[2])


class RolePipelinesTests(RoleMiddlewareTests):
    """The same role behaviour with one separate chain per role."""

    configure = staticmethod(configure_role_pipelines)

    async def test_a_role_without_a_chain_fails_before_any_agent_runs(self) -> None:
        agent = ScriptedAgentClient()
        host = self.host(agent, ScriptedChecks())
        try:
            with self.assertRaisesRegex(LookupError, "tester"):
                await host.run(request(role="tester"))
        finally:
            await host.aclose()
        self.assertEqual([], agent.prompts)


if __name__ == "__main__":
    unittest.main()
