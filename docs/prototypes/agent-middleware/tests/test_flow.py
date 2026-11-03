from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from agentmw.flow import (
    Fail,
    Flow,
    Hold,
    Item,
    Pass,
    Selector,
    Stage,
    Verdict,
    Workflow,
    handoff,
    next_,
    rework,
    stop,
)


@dataclass
class Ctx:
    item: Any = None
    results: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)


def stage(name: str, *verdicts: Verdict) -> Stage[Ctx]:
    remaining = list(verdicts)

    async def action(ctx: Ctx) -> Verdict:
        ctx.log.append(name)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return Stage(name, action)


async def noted(ctx: Ctx, reason: str) -> None:
    ctx.log.append(f"handoff: {reason}")


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_stages_in_order_and_keeps_their_results(self) -> None:
        ctx = Ctx()

        stopped = (
            await Flow("f")
            .stage(stage("a", Pass(1)))
            .stage(stage("b", Pass(2)))
            .run(ctx)
        )

        self.assertIsNone(stopped)
        self.assertEqual({"a": 1, "b": 2}, ctx.results)

    async def test_an_unanswered_fail_or_hold_ends_the_run(self) -> None:
        for verdict, status in ((Fail("x"), "failed"), (Hold("x"), "held")):
            ctx = Ctx()
            flow = Flow("f").stage(stage("a", verdict)).stage(stage("b", Pass()))

            stopped = await flow.run(ctx)

            self.assertEqual((status, "x"), (stopped.status, stopped.reason))
            self.assertEqual(["a"], ctx.log)

    async def test_responses_stop_idle_hand_off_or_skip(self) -> None:
        flow = (
            Flow("f")
            .stage(stage("held", Hold("h")).on_hold(stop.idle()))
            .stage(stage("never", Pass()))
        )
        self.assertEqual("idle", (await flow.run(Ctx())).status)

        ctx = Ctx()
        flow = Flow("f").stage(stage("a", Fail("r")).on_fail(handoff(noted)))
        self.assertEqual("handed_off", (await flow.run(ctx)).status)
        self.assertEqual(["a", "handoff: r"], ctx.log)

        ctx = Ctx()
        flow = (
            Flow("f")
            .stage(stage("a", Fail("r")).on_fail(next_()))
            .stage(stage("b", Pass()))
        )
        self.assertIsNone(await flow.run(ctx))
        self.assertEqual(["a", "b"], ctx.log)
        self.assertEqual({"a": "r"}, ctx.failures)

    async def test_rework_asks_again_then_falls_back(self) -> None:
        ctx = Ctx()
        flaky = stage("a", Fail("1"), Pass("ok")).on_fail(rework(max_attempts=2))
        self.assertIsNone(await Flow("f").stage(flaky).run(ctx))
        self.assertEqual(["a", "a"], ctx.log)

        ctx = Ctx()
        broken = stage("a", Fail("no")).on_fail(
            rework(max_attempts=2, then=handoff(noted, status="escalated"))
        )
        stopped = await Flow("f").stage(broken).run(ctx)
        self.assertEqual("escalated", stopped.status)
        self.assertEqual(["a", "a", "handoff: no"], ctx.log)

    async def test_when_skips_a_stage(self) -> None:
        async def never(ctx: Ctx) -> bool:
            return False

        ctx = Ctx()

        await Flow("f").stage(stage("a", Fail("x")).when(never)).run(ctx)

        self.assertEqual([], ctx.log)

    async def test_each_rereads_and_next_skips_a_failed_item(self) -> None:
        pending = [1, 2, 3]

        async def find(ctx: Ctx) -> list[Item]:
            return [Item(n, str(n)) for n in pending]

        async def work(ctx: Ctx) -> Verdict:
            ctx.log.append(f"item {ctx.item}")
            if ctx.item == 2:
                return Fail("bad")
            pending.remove(ctx.item)
            return Pass()

        ctx = Ctx()
        each = Flow("each").stage(Stage("work", work))

        flow = Flow("f").each(Selector("pending", find), each, on_fail=next_())

        self.assertIsNone(await flow.run(ctx))
        self.assertEqual(["item 1", "item 2", "item 3"], ctx.log)

    async def test_each_without_on_fail_propagates_the_stop(self) -> None:
        async def find(ctx: Ctx) -> list[Item]:
            return [Item(1, "1")]

        flow = Flow("f").each(
            Selector("one", find), Flow("e").stage(stage("a", Fail("x")))
        )

        self.assertEqual("failed", (await flow.run(Ctx())).status)

    async def test_on_error_answers_an_escaping_error(self) -> None:
        async def boom(ctx: Ctx) -> Verdict:
            raise LookupError("gone")

        ctx = Ctx()
        workflow = (
            Workflow("w")
            .on_error((LookupError,), handoff(noted, status="failed"))
            .stage(Stage("boom", boom))
        )

        stopped = await workflow.run(ctx)

        self.assertEqual(("failed", "gone"), (stopped.status, stopped.reason))
        self.assertEqual(["handoff: gone"], ctx.log)

    async def test_an_unregistered_error_propagates(self) -> None:
        async def boom(ctx: Ctx) -> Verdict:
            raise KeyError("x")

        with self.assertRaises(KeyError):
            await Workflow("w").stage(Stage("boom", boom)).run(Ctx())


if __name__ == "__main__":
    unittest.main()
