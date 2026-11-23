from __future__ import annotations

import unittest

from agentmw import AgentCallContext, AgentRequest, Next, PipelineBuilder
from dishka import Provider, Scope, make_async_container, provide


class Trace:
    def __init__(self) -> None:
        self.events: list[str] = []


class First:
    def __init__(self, trace: Trace) -> None:
        self._trace = trace

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        self._trace.events.append("first:before")
        await next(ctx)
        self._trace.events.append("first:after")


class Second:
    def __init__(self, trace: Trace) -> None:
        self._trace = trace

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        self._trace.events.append("second")
        await next(ctx)


class ShortCircuit:
    def __init__(self, trace: Trace) -> None:
        self._trace = trace

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        self._trace.events.append("short")


class Endpoint:
    def __init__(self, trace: Trace) -> None:
        self._trace = trace

    async def handle(self, ctx: AgentCallContext) -> None:
        self._trace.events.append("endpoint")


class OtherEndpoint:
    def __init__(self, trace: Trace) -> None:
        self._trace = trace

    async def handle(self, ctx: AgentCallContext) -> None:
        self._trace.events.append("other-endpoint")


class Counter:
    created = 0

    def __init__(self) -> None:
        Counter.created += 1

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        await next(ctx)


class Wiring(Provider):
    scope = Scope.REQUEST

    trace = provide(Trace, scope=Scope.APP)
    first = provide(First)
    second = provide(Second)
    short = provide(ShortCircuit)
    endpoint = provide(Endpoint)
    other = provide(OtherEndpoint)
    counter = provide(Counter)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.container = make_async_container(Wiring())
        self.trace = await self.container.get(Trace)

    async def asyncTearDown(self) -> None:
        await self.container.close()

    async def call(self, app: PipelineBuilder, role: str = "dev") -> None:
        pipeline = app.build()
        async with self.container() as services:
            await pipeline(AgentCallContext(AgentRequest(role, "", str), services))

    async def test_runs_middlewares_in_registration_order_around_the_terminal(
        self,
    ) -> None:
        app = PipelineBuilder().use(First).use(Second).run(Endpoint)

        await self.call(app)

        self.assertEqual(
            ["first:before", "second", "endpoint", "first:after"], self.trace.events
        )

    async def test_a_middleware_that_skips_next_short_circuits(self) -> None:
        app = PipelineBuilder().use(First).use(ShortCircuit).use(Second).run(Endpoint)

        await self.call(app)

        self.assertEqual(["first:before", "short", "first:after"], self.trace.events)

    async def test_for_role_runs_its_branch_only_for_that_role_then_rejoins(
        self,
    ) -> None:
        app = PipelineBuilder().for_role("reviewer", lambda b: b.use(Second))
        app.run(Endpoint)

        await self.call(app, role="reviewer")
        await self.call(app, role="dev")

        self.assertEqual(["second", "endpoint", "endpoint"], self.trace.events)

    async def test_map_when_diverts_to_its_own_terminal(self) -> None:
        app = PipelineBuilder().map_when(
            lambda ctx: ctx.role == "fake", lambda b: b.run(OtherEndpoint)
        )
        app.use(First).run(Endpoint)

        await self.call(app, role="fake")

        self.assertEqual(["other-endpoint"], self.trace.events)

    async def test_map_when_without_terminal_uses_the_parent_terminal(self) -> None:
        app = PipelineBuilder().map_when(lambda ctx: True, lambda b: b.use(Second))
        app.use(First).run(Endpoint)

        await self.call(app)

        self.assertEqual(["second", "endpoint"], self.trace.events)

    async def test_resolves_a_new_middleware_instance_per_call(self) -> None:
        app = PipelineBuilder().use(Counter).run(Endpoint)
        before = Counter.created

        await self.call(app)
        await self.call(app)

        self.assertEqual(2, Counter.created - before)

    def test_building_without_a_terminal_raises(self) -> None:
        with self.assertRaises(ValueError):
            PipelineBuilder().use(First).build()

    async def test_map_role_gives_each_role_its_own_chain(self) -> None:
        def shared(chain: PipelineBuilder) -> None:
            chain.use(First)

        app = PipelineBuilder()
        app.map_role("planner", lambda b: b.apply(shared).run(Endpoint))
        app.map_role("reviewer", lambda b: b.use(Second).run(OtherEndpoint))
        app.run(Endpoint)

        await self.call(app, role="planner")
        await self.call(app, role="reviewer")
        await self.call(app, role="other")

        self.assertEqual(
            [
                "first:before",
                "endpoint",
                "first:after",
                "second",
                "other-endpoint",
                "endpoint",
            ],
            self.trace.events,
        )


if __name__ == "__main__":
    unittest.main()
