from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentmw import (
    AgentCallContext,
    AgentClient,
    AgentDeclined,
    AgentRequest,
    AgentResult,
    CheckResult,
    CheckRunner,
    ReplyMismatch,
    ReplyRejected,
    RetryPolicy,
    WorktreeGit,
    bind,
)
from agentmw.fakes import ScriptedChecks
from agentmw.implement_spec import (
    HITL,
    Delivered,
    ImplementSpec,
    Proposed,
    ProposedTicket,
    Refined,
    SpecRoute,
    Tracker,
    create_implement_spec_host,
)
from agentmw.implement_spec.fakes import FakeGit, FakeTracker, ScriptedFactoryAgent
from agentmw.implement_spec.middlewares import (
    ChoreCommitMiddleware,
    DiffCaptureMiddleware,
    PlanPolicyMiddleware,
)
from agentmw.implement_spec.policy import PlanPolicy, chore_subject
from agentmw.middlewares import (
    AgentCommitsMiddleware,
    JsonReplyMiddleware,
    ReadOnlyMiddleware,
    ReplyRetryMiddleware,
    StatusEnvelopeMiddleware,
)
from agentmw.middlewares.json_reply import to_typed

WORKTREE = "/worktree"


def context(reply_type: type, **metadata: str) -> AgentCallContext:
    # Middlewares under test never touch the container.
    services: Any = None
    request = AgentRequest("role", "", reply_type, metadata=metadata)
    return AgentCallContext(request, services)


def returning(*outputs: str):
    remaining = list(outputs)

    async def terminal(ctx: AgentCallContext) -> None:
        ctx.result = AgentResult(remaining.pop(0), 0)

    return terminal


def envelope(status: str, result: dict, **extra: str) -> str:
    return "narration\n" + json.dumps({**extra, "status": status, "result": result})


async def decode(ctx: AgentCallContext, output: str) -> None:
    await JsonReplyMiddleware().invoke(
        ctx, lambda c: StatusEnvelopeMiddleware().invoke(c, returning(output))
    )


class StatusEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_decodes_the_completed_result_into_the_reply_type(self) -> None:
        ctx = context(Proposed)

        await decode(ctx, envelope("completed", {"tickets": [{"title": "A"}]}))

        self.assertEqual(Proposed((ProposedTicket("A"),)), ctx.reply)

    async def test_failed_is_a_decline_with_its_reason(self) -> None:
        with self.assertRaises(AgentDeclined) as raised:
            await decode(context(Proposed), envelope("failed", {"reason": "unclear"}))

        self.assertEqual("unclear", raised.exception.reason)

    async def test_failed_without_a_reason_is_a_mismatch(self) -> None:
        with self.assertRaisesRegex(ReplyMismatch, "reason"):
            await decode(context(Proposed), envelope("failed", {}))

    async def test_a_different_identifier_is_a_mismatch(self) -> None:
        ctx = context(Refined, identifier="i|1")

        with self.assertRaisesRegex(ReplyMismatch, "identifier"):
            await decode(ctx, envelope("completed", {}, identifier="i|2"))

    async def test_an_omitted_identifier_is_accepted(self) -> None:
        ctx = context(Refined, identifier="i|1")

        await decode(ctx, envelope("completed", {}))

        self.assertEqual(Refined(), ctx.reply)

    async def test_a_decline_is_not_retried(self) -> None:
        ctx = context(Proposed)
        attempts = 0

        async def declining(c: AgentCallContext) -> None:
            nonlocal attempts
            attempts += 1
            await decode(c, envelope("failed", {"reason": "no"}))

        with self.assertRaises(AgentDeclined):
            await ReplyRetryMiddleware(RetryPolicy(3)).invoke(ctx, declining)
        self.assertEqual(1, attempts)

    def test_fields_with_defaults_may_be_omitted(self) -> None:
        self.assertEqual(
            ProposedTicket("A", "", (0,)),
            to_typed(ProposedTicket, {"title": "A", "blocked_by": [0]}),
        )


class PlanPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, *tickets: ProposedTicket, limit: int = 15) -> None:
        async def planned(ctx: AgentCallContext) -> None:
            ctx.reply = Proposed(tickets)

        await PlanPolicyMiddleware(PlanPolicy(limit)).invoke(context(Proposed), planned)

    async def test_accepts_tickets_blocked_by_earlier_ones(self) -> None:
        await self.check(ProposedTicket("A"), ProposedTicket("B", blocked_by=(0,)))

    async def test_rejects_a_forward_blocker(self) -> None:
        with self.assertRaisesRegex(ReplyRejected, "does not come before"):
            await self.check(ProposedTicket("A", blocked_by=(1,)), ProposedTicket("B"))

    async def test_rejects_an_empty_or_oversized_plan(self) -> None:
        with self.assertRaisesRegex(ReplyRejected, "no Tickets"):
            await self.check()
        with self.assertRaisesRegex(ReplyRejected, "limit is 1"):
            await self.check(ProposedTicket("A"), ProposedTicket("B"), limit=1)


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.git = FakeGit()
        self.base = await self.git.head(WORKTREE)

    def developer(self) -> AgentCallContext:
        return context(Delivered, worktree=WORKTREE, commit_prefix="ccode(i|1): ")

    def committing(self, *subjects: str, report: str | None = None):
        async def agent(ctx: AgentCallContext) -> None:
            sha = ""
            for subject in subjects:
                sha = self.git.commit_now(WORKTREE, subject)
            ctx.reply = Delivered(report or sha, "s", "v")

        return agent

    async def test_one_prefixed_reported_commit_is_kept(self) -> None:
        ctx = self.developer()

        await AgentCommitsMiddleware(self.git).invoke(
            ctx, self.committing("ccode(i|1): x")
        )

        self.assertEqual(1, len(self.git.log(WORKTREE)))
        self.assertEqual("ccode(i|1): ", ctx.args["COMMIT_SUBJECT_PREFIX"])

    async def test_violations_are_rejected_and_restored(self) -> None:
        cases = {
            "exactly one": self.committing("ccode(i|1): a", "ccode(i|1): b"),
            "lacks the prefix": self.committing("feat: x"),
            "not the head": self.committing("ccode(i|1): x", report="other"),
        }
        for message, agent in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ReplyRejected, message):
                await AgentCommitsMiddleware(self.git).invoke(self.developer(), agent)
            self.assertEqual(self.base, await self.git.head(WORKTREE))

    async def test_a_decline_restores_what_the_agent_committed(self) -> None:
        async def declining(ctx: AgentCallContext) -> None:
            self.git.commit_now(WORKTREE, "ccode(i|1): half")
            raise AgentDeclined("developer", "stuck")

        with self.assertRaises(AgentDeclined):
            await AgentCommitsMiddleware(self.git).invoke(self.developer(), declining)
        self.assertEqual([], self.git.log(WORKTREE))

    async def test_read_only_rejects_and_restores_an_edit(self) -> None:
        async def editing(ctx: AgentCallContext) -> None:
            self.git.edit(WORKTREE, "a.py")

        with self.assertRaisesRegex(ReplyRejected, "read-only"):
            await ReadOnlyMiddleware(self.git).invoke(
                context(Refined, worktree=WORKTREE), editing
            )
        self.assertEqual("", await self.git.status(WORKTREE))


class ChoreCommitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.git = FakeGit()

    async def run_chore(self, agent, checks: CheckRunner | None = None) -> None:
        ctx = context(Refined, worktree=WORKTREE, initiative="basket")
        middleware = ChoreCommitMiddleware(self.git, checks or ScriptedChecks())
        await middleware.invoke(ctx, agent)

    def editing(self, message: str = "tidy names"):
        async def agent(ctx: AgentCallContext) -> None:
            self.git.edit(WORKTREE, "a.py")
            ctx.reply = Refined(message, "renamed")

        return agent

    async def test_loop_commits_the_edits_under_the_chore_subject(self) -> None:
        await self.run_chore(self.editing())

        self.assertEqual(
            ["chore(basket): tidy names"], [c.subject for c in self.git.log(WORKTREE)]
        )

    async def test_no_edit_makes_no_commit(self) -> None:
        async def idle(ctx: AgentCallContext) -> None:
            ctx.reply = Refined()

        await self.run_chore(idle)

        self.assertEqual([], self.git.log(WORKTREE))

    async def test_an_agent_commit_is_rejected_and_restored(self) -> None:
        async def committing(ctx: AgentCallContext) -> None:
            self.git.commit_now(WORKTREE, "mine")
            ctx.reply = Refined()

        with self.assertRaisesRegex(ReplyRejected, "only Loop commits"):
            await self.run_chore(committing)
        self.assertEqual([], self.git.log(WORKTREE))

    async def test_failing_verification_discards_the_edits(self) -> None:
        checks = ScriptedChecks(CheckResult(False, "1 failed"))

        with self.assertRaisesRegex(ReplyRejected, "1 failed"):
            await self.run_chore(self.editing(), checks)
        self.assertEqual("", await self.git.status(WORKTREE))

    def test_chore_subject_falls_back_and_drops_a_repeated_prefix(self) -> None:
        self.assertEqual("chore(b): refine the change set", chore_subject("b", ""))
        self.assertEqual("chore(b): x", chore_subject("b", "chore(b): x\nmore"))


class DiffCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_diff_exists_only_during_the_call(self) -> None:
        tracker = FakeTracker(1, "Spec", "")
        pull_request = await tracker.publish_draft("feature", "Spec")
        seen: list[str] = []

        async def agent(ctx: AgentCallContext) -> None:
            seen.append((Path(ctx.args["DIFF_DIR"]) / "_manifest.tsv").read_text())

        with tempfile.TemporaryDirectory() as root:
            ctx = context(Refined, worktree=root)
            ctx.items["pull_request"] = pull_request
            await DiffCaptureMiddleware(tracker).invoke(ctx, agent)
            self.assertFalse(Path(ctx.args["DIFF_DIR"]).exists())

        self.assertIn("src/discount.py\tsrc_discount.py", seen[0])


class ImplementSpecTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        root = Path(self.root.name)
        (root / "checkout").mkdir()
        (root / "worktree").mkdir()
        self.route = SpecRoute(
            1,
            "basket",
            "main",
            "basket",
            str(root / "checkout"),
            str(root / "worktree"),
        )
        self.git = FakeGit()
        self.tracker = FakeTracker(1, "Basket: Discount", "Give 10% off.")

    async def asyncTearDown(self) -> None:
        self.root.cleanup()

    async def run_spec(self, agent: AgentClient, *extra):
        host = create_implement_spec_host(
            bind(Tracker, self.tracker),
            bind(WorktreeGit, self.git),
            bind(AgentClient, agent),
            *extra,
        )
        try:
            return await ImplementSpec(host, self.tracker, self.git).run(self.route)
        finally:
            await host.aclose()

    def roles(self, agent: ScriptedFactoryAgent) -> list[str]:
        return [role for role, _ in agent.calls]

    async def test_delivers_refines_reviews_and_marks_ready(self) -> None:
        agent = ScriptedFactoryAgent(self.git, fail_first={11})

        outcome = await self.run_spec(agent)

        self.assertEqual("ready", outcome.status)
        self.assertEqual(
            [
                "planner",
                "planner",
                "developer",
                "developer",
                "developer",
                "chore",
                "reviewer",
            ],
            self.roles(agent),
        )
        subjects = [c.subject for c in self.git.log(self.route.worktree)]
        self.assertEqual(
            ["ccode(basket|10): ", "ccode(basket|11): ", "chore(basket): "],
            [subject[: subject.index(": ") + 2] for subject in subjects],
        )
        self.assertIn("rejected", agent.prompts("planner")[1])
        self.assertIn("ccode(basket|10)", agent.prompts("developer")[1])

    async def test_a_ticket_declined_every_attempt_is_escalated(self) -> None:
        agent = ScriptedFactoryAgent(self.git, decline={10})

        outcome = await self.run_spec(agent)

        self.assertEqual("failed", outcome.status)
        self.assertIn("#10", outcome.reason)
        self.assertEqual(
            ["planner", "planner", "developer", "developer"], self.roles(agent)
        )
        self.assertIn(HITL, self.tracker.labels(10))
        self.assertIn(HITL, self.tracker.labels(1))
        self.assertEqual([], self.git.log(self.route.worktree))

    async def test_an_invalid_plan_hands_the_spec_to_a_person(self) -> None:
        agent = ScriptedFactoryAgent(self.git, flaky_plan=False, plan=())

        outcome = await self.run_spec(agent)

        self.assertEqual("handed_off", outcome.status)
        self.assertIn("no Tickets", self.tracker.comments(1)[0])
        self.assertIn(HITL, self.tracker.labels(1))

    async def test_a_blocking_finding_keeps_the_pull_request_a_draft(self) -> None:
        finding = {"axis": "bugs", "kind": "bug", "summary": "off by one"}
        agent = ScriptedFactoryAgent(self.git, findings=[finding])

        outcome = await self.run_spec(agent)

        self.assertEqual("draft", outcome.status)
        self.assertIn("1 unresolved", outcome.reason)
        self.assertIn(HITL, self.tracker.labels(1))

    async def test_a_failing_chore_is_discarded_and_the_delivery_stands(self) -> None:
        agent = ScriptedFactoryAgent(self.git)
        checks = bind(CheckRunner, ScriptedChecks(CheckResult(False, "lint")))

        outcome = await self.run_spec(agent, checks)

        self.assertEqual("ready", outcome.status)
        self.assertEqual(2, len(self.git.log(self.route.worktree)))


if __name__ == "__main__":
    unittest.main()
