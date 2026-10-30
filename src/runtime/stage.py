"""The Stage value: an immutable definition whose every modifier returns a modified copy."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import UnionType
from typing import Any, TypeAliasType

from ..builder import AgentWorkspace

from ..agents.contract import Message, TypedAgent

from .event import DefinitionError, Event
from .facts import event_shape, input_fields, is_event_class
from .routes import Route, Routes, is_route

KEBAB_CASE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")


@dataclass(frozen=True)
class Nothing(Event):
    """The reply of a gate-only Stage."""


@dataclass(frozen=True)
class Pass[P: Event]:
    """The gate accepts the attempt; `event` is what later Stages read."""

    event: P


@dataclass(frozen=True)
class Fail[E: Event]:
    """The gate rejects the attempt; `event` may carry evidence for later Stages."""

    reason: str
    event: E | None = None


@dataclass(frozen=True)
class Hold[E: Event]:
    """The gate cannot decide until something outside changes; `event` may carry evidence for later Stages."""

    reason: str
    event: E | None = None


type Verdict[P] = Pass[P] | Fail[Any] | Hold[Any]


@dataclass(frozen=True)
class Evidence:
    """Git facts the runtime observed after the executor ran; gates read it and never collect it."""

    base: str
    head: str
    changed: bool


type Gate[I, R, P] = Callable[[I, R, Evidence], Verdict[P]]
type Verify = Callable[[Path], Fail[Any] | None]
type ClosedSum = TypeAliasType | UnionType


@dataclass(frozen=True)
class AgentExecution[I, Q: Message, R]:
    """Runs one role agent in the enclosing workspace."""

    agent: TypedAgent[Q, R]
    request: Callable[[I, Path], Q]
    session: Callable[[I], str | None] = lambda _: None


@dataclass(frozen=True)
class Action[I, R]:
    """Deterministic Workflow Action with no external effect; re-run freely and never replayed."""

    run: Callable[[I], R]


@dataclass(frozen=True)
class Effect[I, R]:
    """Workflow Action with an externally visible effect; `reconcile` recognizes an effect a crashed attempt already made."""

    run: Callable[[I], R]
    reconcile: Callable[[I], R | None]


type Executor[I, R] = AgentExecution[I, Any, R] | Action[I, R] | Effect[I, R] | None


@dataclass(frozen=True)
class NoRepository:
    """The Stage does not touch a worktree."""


@dataclass(frozen=True)
class ReadOnly:
    """HEAD and the git status must equal their values before the attempt."""


@dataclass(frozen=True)
class AgentCommits[I, P]:
    """Exactly one agent commit with the subject prefix `subject(input)`, a clean tree, and `reported(event)` equal to HEAD."""

    subject: Callable[[I], str]
    reported: Callable[[P], str]


@dataclass(frozen=True)
class Restore:
    """Reset to the attempt base after a failed attempt."""


@dataclass(frozen=True)
class Retain:
    """Leave a failed attempt's worktree for inspection; such a Stage cannot be reworked."""


@dataclass(frozen=True)
class HarnessCommits[I, P]:
    """The agent never moves HEAD; on pass the runtime verifies, then commits `message` if anything changed."""

    message: Callable[[I, P], str]
    verify: Verify | None = None
    on_fail: Restore | Retain = field(default_factory=Restore)


type WorkContract = NoRepository | ReadOnly | AgentCommits[Any, Any] | HarnessCommits[Any, Any]


@dataclass(frozen=True)
class Limit:
    """At most `attempts` started attempts per key, across epochs."""

    attempts: int
    per: Callable[[Any], str]


@dataclass(frozen=True)
class Item[T]:
    """One unit of an `each` loop: its value, a stable id, and the revision its evidence is bound to."""

    value: T
    id: str
    revision: str


@dataclass(frozen=True)
class Selector[I, T]:
    """Pure: given the scope's facts, the candidate items in order, possibly without end; re-run after every item, the loop takes the first it has not run, and never an effect."""

    id: str
    input: type[I]
    item: type[T]
    select: Callable[[I], Iterable[Item[T]]]

    def __post_init__(self) -> None:
        """Rejects a non-kebab-case id and an input that cannot resolve."""
        _require_kebab(f"Selector '{self.id}'", self.id)
        input_fields(f"Selector '{self.id}'", self.input)


@dataclass(frozen=True)
class WorkspaceOpener[I]:
    """Opens the AgentWorkspace a `within` scope runs in; `id` names the scope."""

    id: str
    input: type[I]
    open: Callable[[I], AbstractContextManager[AgentWorkspace]]

    def __post_init__(self) -> None:
        """Rejects a non-kebab-case id and an input that cannot resolve."""
        _require_kebab(f"Workspace opener '{self.id}'", self.id)
        input_fields(f"Workspace opener '{self.id}'", self.input)


def _require_kebab(owner: str, identity: str) -> None:
    """Raises DefinitionError unless `identity` is a kebab-case identity."""
    if not KEBAB_CASE.fullmatch(identity):
        raise DefinitionError(f"{owner}: identity must be kebab-case, such as 'implement-ticket'")


@dataclass(frozen=True)
class Stage[I, R, P: Event]:
    """One Stage definition; every method returns a modified copy, so a library default is never mutated by a Workflow."""

    id: str
    input: type[I]
    produces: type[P] | ClosedSum
    may_emit: tuple[type[Event], ...] = ()
    executor: Executor[I, R] = None
    judge: Gate[I, R, P] | None = None
    work: WorkContract = field(default_factory=NoRepository)
    routes: Routes = field(default_factory=Routes)
    when_: Callable[[I], bool] | None = None
    revision_: Callable[[I], str] | None = None
    limit_: Limit | None = None
    once_: Callable[[I], str] | None = None
    reads_: tuple[tuple[type[Event], str], ...] = ()
    run_records_: Callable[[Any, str | None], Mapping[str, str | int | bool]] | None = None
    describe: Callable[[I], Mapping[str, str | int | bool]] = lambda _: {}

    def __post_init__(self) -> None:
        """Boundary validation: kebab-case id, a resolvable frozen dataclass input, and declared Events."""
        owner = f"Stage '{self.id}'"
        _require_kebab(owner, self.id)
        input_fields(owner, self.input)
        if event_shape(self.produces) is None:
            raise DefinitionError(f"{owner}: produces must be an Event or a closed sum of Events, not {self.produces!r}")
        for event in self.may_emit:
            if not is_event_class(event):
                raise DefinitionError(f"{owner}: may_emit lists Events, not {event!r}")
        for route in (self.routes.passed, self.routes.failed, self.routes.held):
            if not is_route(route):
                raise DefinitionError(f"{owner}: {route!r} is not a route")

    def agent[Q: Message, R2](
        self, agent: TypedAgent[Q, R2], request: Callable[[I, Path], Q], *, session: Callable[[I], str | None] = lambda _: None,
    ) -> Stage[I, R2, P]:
        """Runs a role agent in the enclosing workspace."""
        return self._with_executor(AgentExecution(agent, request, session))

    def action[R2](self, run: Callable[[I], R2]) -> Stage[I, R2, P]:
        """Runs a deterministic Workflow Action with no external effect."""
        return self._with_executor(Action(run))

    def effect[R2](self, run: Callable[[I], R2], *, reconcile: Callable[[I], R2 | None]) -> Stage[I, R2, P]:
        """Runs a Workflow Action with an external effect and the reconcile that recognizes a crashed attempt's effect."""
        return self._with_executor(Effect(run, reconcile))

    def _with_executor(self, executor: Any) -> Any:
        """A copy with `executor`; a Stage has at most one."""
        if self.executor is not None:
            raise DefinitionError(f"Stage '{self.id}': already has an executor; a Stage runs at most one")
        return replace(self, executor=executor)

    def gate[P2: Event](self, gate: Gate[I, R, P2]) -> Stage[I, R, P2]:
        """Sets the trusted gate that turns the reply into pass, fail or hold."""
        return replace(self, judge=gate)  # type: ignore[arg-type]

    def contract(self, work: WorkContract) -> Stage[I, R, P]:
        """Sets the repository contract."""
        return replace(self, work=work)

    def on_pass(self, route: Route) -> Stage[I, R, P]:
        """Sets where a pass goes."""
        return replace(self, routes=replace(self.routes, passed=route))

    def on_fail(self, route: Route) -> Stage[I, R, P]:
        """Sets where a fail goes."""
        return replace(self, routes=replace(self.routes, failed=route))

    def on_hold(self, route: Route) -> Stage[I, R, P]:
        """Sets where a hold goes."""
        return replace(self, routes=replace(self.routes, held=route))

    def when(self, predicate: Callable[[I], bool]) -> Stage[I, R, P]:
        """Skips the Stage, producing nothing, when `predicate` is false."""
        return replace(self, when_=predicate)

    def revision(self, revision: Callable[[I], str]) -> Stage[I, R, P]:
        """Binds the Stage's evidence to a revision such as a head SHA."""
        return replace(self, revision_=revision)

    def limit(self, attempts: int, *, per: Callable[[I], str]) -> Stage[I, R, P]:
        """Allows at most `attempts` started attempts per key across epochs."""
        return replace(self, limit_=Limit(attempts, per))

    def once(self, *, per: Callable[[I], str]) -> Stage[I, R, P]:
        """Allows at most one pass per key across runs; only an agent or an effect Stage can be once-per-key."""
        return replace(self, once_=per)

    def named(self, id: str) -> Stage[I, R, P]:
        """The same logic placed a second time under a new stable identity."""
        return replace(self, id=id)

    def reads[T: Event](self, event: type[T], *, from_: str) -> Stage[I, R, P]:
        """Names the producer of an Event that two or more Stages produce."""
        return replace(self, reads_=(*self.reads_, (event, from_)))

    def records_run(self, attributes: Callable[[P, str | None], Mapping[str, str | int | bool]]) -> Stage[I, R, P]:
        """Sets the run attributes a hand-off through this Stage records from its Event and the reason the route gave; a run's reader, such as an intake, finds them on the closed run."""
        return replace(self, run_records_=attributes)

    def tune(self, **profile_fields: Any) -> Stage[I, R, P]:
        """Tunes the agent's profile; applies to an agent Stage only."""
        if not isinstance(self.executor, AgentExecution):
            raise DefinitionError(f"Stage '{self.id}': tune applies to an agent Stage only")
        tuned = replace(self.executor, agent=self.executor.agent.tuned(**profile_fields))
        return replace(self, executor=tuned)

    def attributes(self, describe: Callable[[I], Mapping[str, str | int | bool]]) -> Stage[I, R, P]:
        """Sets the span attributes derived from the input."""
        return replace(self, describe=describe)
