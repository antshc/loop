"""A declarative Workflow builder over async Stages, after the software factory's `Workflow` and `Flow`."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import Any, Protocol, Self

logger = logging.getLogger("agentmw.flow")


class Context(Protocol):
    """What the engine reads and writes on a Workflow's context."""

    item: Any
    results: dict[str, Any]
    failures: dict[str, str]


@dataclass(frozen=True)
class Pass:
    """The Stage succeeded; `value` is kept in `ctx.results[stage.name]`."""

    value: Any = None


@dataclass(frozen=True)
class Hold:
    """The Stage needs a decision it cannot make."""

    reason: str


@dataclass(frozen=True)
class Fail:
    """The Stage broke a rule."""

    reason: str


type Verdict = Pass | Hold | Fail


@dataclass(frozen=True)
class Stop:
    """Why a run ended before its last step: `status` and the reason."""

    status: str
    reason: str = ""


type Handler[C] = Callable[[C, str], Awaitable[None]]


@dataclass(frozen=True)
class Next:
    """Carry on with the next Stage, or the next item of an `each`."""


@dataclass(frozen=True)
class StopWith:
    """End the run with `status`."""

    status: str


@dataclass(frozen=True)
class Handoff[C]:
    """Run `action`, then end the run with `status`."""

    action: Handler[C]
    status: str


@dataclass(frozen=True)
class Rework:
    """Run the Stage again up to `max_attempts` times in all, then respond with `then`."""

    max_attempts: int
    then: Response | None = None


type Response = Next | StopWith | Handoff[Any] | Rework


def next_() -> Next:
    """Skip the failed Stage or item."""
    return Next()


class _Stops:
    def idle(self) -> StopWith:
        """End the run with nothing to do."""
        return StopWith("idle")


stop = _Stops()


def handoff[C](action: Handler[C], *, status: str = "handed_off") -> Handoff[C]:
    """Run `action(ctx, reason)` and end the run."""
    return Handoff(action, status)


def rework(*, max_attempts: int, then: Response | None = None) -> Rework:
    """Ask the Stage again, then fall back to `then`."""
    return Rework(max_attempts, then)


@dataclass(frozen=True)
class Stage[C]:
    """One named step: an async action that returns a verdict, and what to do with a hold or a failure."""

    name: str
    action: Callable[[C], Awaitable[Verdict]]
    hold: Response | None = None
    fail: Response | None = None
    condition: Callable[[C], Awaitable[bool]] | None = None

    def named(self, name: str) -> Stage[C]:
        """The same Stage under another name, so its result can be told apart."""
        return replace(self, name=name)

    def on_hold(self, response: Response) -> Stage[C]:
        """What a `Hold` does; without it the run ends `held`."""
        return replace(self, hold=response)

    def on_fail(self, response: Response) -> Stage[C]:
        """What a `Fail` does; without it the run ends `failed`."""
        return replace(self, fail=response)

    def when(self, condition: Callable[[C], Awaitable[bool]]) -> Stage[C]:
        """Skips the Stage unless `condition` holds."""
        return replace(self, condition=condition)


@dataclass(frozen=True)
class Item:
    """One thing an `each` works through; `key` stops it being taken twice."""

    value: Any
    key: str


@dataclass(frozen=True)
class Selector[C]:
    """Re-reads the items to work through before every pass of an `each`."""

    name: str
    find: Callable[[C], Awaitable[Sequence[Item]]]


type Step[C] = Callable[[C], Awaitable[Stop | None]]
type Workspace[C] = Callable[[C], AbstractAsyncContextManager[None]]


class Flow[C: Context]:
    """An ordered list of Stages, each-loops and workspaces."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._steps: list[Step[C]] = []

    def stage(self, stage: Stage[C]) -> Self:
        """Runs `stage`; a hold or failure is answered by its responses."""

        async def step(ctx: C) -> Stop | None:
            return await _run_stage(stage, ctx)

        self._steps.append(step)
        return self

    def each(
        self, selector: Selector[C], flow: Flow[C], *, on_fail: Response | None = None
    ) -> Self:
        """Runs `flow` for each item `selector` finds, re-reading it after every pass."""

        async def step(ctx: C) -> Stop | None:
            return await _run_each(selector, flow, on_fail, ctx)

        self._steps.append(step)
        return self

    def within(self, workspace: Workspace[C], flow: Flow[C]) -> Self:
        """Runs `flow` inside `workspace`."""

        async def step(ctx: C) -> Stop | None:
            async with workspace(ctx):
                return await flow.run(ctx)

        self._steps.append(step)
        return self

    async def run(self, ctx: C) -> Stop | None:
        """Runs the steps in order; the first `Stop` ends the flow, None means it completed."""
        for step in self._steps:
            stopped = await step(ctx)
            if stopped is not None:
                return stopped
        return None


class Workflow[C: Context](Flow[C]):
    """The top-level Flow, which also answers the errors that escape its Stages."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._errors: list[tuple[tuple[type[Exception], ...], Response]] = []

    def on_error(self, errors: tuple[type[Exception], ...], response: Response) -> Self:
        """Answers `errors` escaping any step with `response`."""
        self._errors.append((errors, response))
        return self

    async def run(self, ctx: C) -> Stop | None:
        """Runs the flow; a registered error is answered instead of raised."""
        handled = tuple(error for errors, _ in self._errors for error in errors)
        try:
            return await super().run(ctx)
        except handled as exception:
            return await self._answer(exception, ctx)

    async def _answer(self, exception: Exception, ctx: C) -> Stop | None:
        for errors, response in self._errors:
            if isinstance(exception, errors):
                return await _respond(response, str(exception), ctx, "failed")
        raise exception


async def _run_stage[C: Context](stage: Stage[C], ctx: C) -> Stop | None:
    if stage.condition is not None and not await stage.condition(ctx):
        return None
    attempt = 0
    while True:
        attempt += 1
        verdict = await stage.action(ctx)
        if isinstance(verdict, Pass):
            ctx.results[stage.name] = verdict.value
            return None
        ctx.failures[stage.name] = verdict.reason
        held = isinstance(verdict, Hold)
        logger.warning(
            "%s %s: %s", stage.name, "held" if held else "failed", verdict.reason
        )
        response = stage.hold if held else stage.fail
        if isinstance(response, Rework):
            if attempt < response.max_attempts:
                continue
            response = response.then
        return await _respond(
            response, verdict.reason, ctx, "held" if held else "failed"
        )


async def _run_each[C: Context](
    selector: Selector[C], flow: Flow[C], on_fail: Response | None, ctx: C
) -> Stop | None:
    done: set[str] = set()
    while (item := _untaken(await selector.find(ctx), done)) is not None:
        done.add(item.key)
        ctx.item = item.value
        stopped = await flow.run(ctx)
        if stopped is not None:
            answer = await _respond(on_fail, stopped.reason, ctx, stopped.status)
            if answer is not None:
                return answer
    return None


def _untaken(items: Sequence[Item], done: set[str]) -> Item | None:
    return next((item for item in items if item.key not in done), None)


async def _respond[C](
    response: Response | None, reason: str, ctx: C, default: str
) -> Stop | None:
    match response:
        case None:
            return Stop(default, reason)
        case Next():
            return None
        case StopWith(status):
            return Stop(status, reason)
        case Handoff(action, status):
            await action(ctx, reason)
            return Stop(status, reason)
    raise ValueError("rework answers a Stage; its fallback cannot be another rework")
