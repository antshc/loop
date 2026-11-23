"""Builds the ordered middleware chain, the `IApplicationBuilder` analogue."""

from __future__ import annotations

from collections.abc import Callable

from .context import AgentCallContext
from .middleware import Middleware, Next, Terminal

type Component = Callable[[Next], Next]
type Predicate = Callable[[AgentCallContext], bool]


class PipelineBuilder:
    """Registers middleware types in order and composes them around one terminal."""

    def __init__(self) -> None:
        self._components: list[Component] = []
        self._terminal: type[Terminal] | None = None

    def use(self, middleware: type[Middleware]) -> PipelineBuilder:
        """Appends a middleware resolved from the call's container on every call."""

        def component(next: Next) -> Next:
            async def step(ctx: AgentCallContext) -> None:
                instance = await ctx.services.get(middleware)
                await instance.invoke(ctx, next)

            return step

        self._components.append(component)
        return self

    def use_when(
        self, predicate: Predicate, configure: Callable[[PipelineBuilder], object]
    ) -> PipelineBuilder:
        """Runs a sub-chain only when `predicate` holds, then rejoins the main chain."""
        branch = PipelineBuilder()
        configure(branch)

        def component(next: Next) -> Next:
            conditional = branch._compose(next)

            async def step(ctx: AgentCallContext) -> None:
                await (conditional if predicate(ctx) else next)(ctx)

            return step

        self._components.append(component)
        return self

    def map_when(
        self, predicate: Predicate, configure: Callable[[PipelineBuilder], object]
    ) -> PipelineBuilder:
        """Diverts matching calls to a separate chain that never rejoins this one."""
        branch = PipelineBuilder()
        configure(branch)

        def component(next: Next) -> Next:
            # Built with the parent's terminal, which is known only at build time.
            diverted = branch.build(default_terminal=self._terminal)

            async def step(ctx: AgentCallContext) -> None:
                await (diverted if predicate(ctx) else next)(ctx)

            return step

        self._components.append(component)
        return self

    def for_role(
        self, role: str, configure: Callable[[PipelineBuilder], object]
    ) -> PipelineBuilder:
        """Adds middlewares that run for one role only."""
        return self.use_when(lambda ctx: ctx.role == role, configure)

    def map_role(
        self, role: str, configure: Callable[[PipelineBuilder], object]
    ) -> PipelineBuilder:
        """Gives one role a chain of its own, ending at its own or this terminal."""
        return self.map_when(lambda ctx: ctx.role == role, configure)

    def apply(self, fragment: Callable[[PipelineBuilder], object]) -> PipelineBuilder:
        """Appends a reusable fragment of middlewares, for sharing steps between chains."""
        fragment(self)
        return self

    def run(self, terminal: type[Terminal]) -> PipelineBuilder:
        """Sets the terminal resolved from the call's container."""
        self._terminal = terminal
        return self

    def build(self, *, default_terminal: type[Terminal] | None = None) -> Next:
        """Composes the chain; a chain without a terminal raises `ValueError`."""
        terminal = self._terminal or default_terminal
        if terminal is None:
            raise ValueError("pipeline has no terminal; call run()")

        async def endpoint(ctx: AgentCallContext) -> None:
            await (await ctx.services.get(terminal)).handle(ctx)

        return self._compose(endpoint)

    def _compose(self, last: Next) -> Next:
        chain = last
        for component in reversed(self._components):
            chain = component(chain)
        return chain
