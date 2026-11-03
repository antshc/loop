"""The four constructs a Flow is a sequence of."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .routes import Handoff, Route, Stop
from .stage import Selector, Stage, WorkspaceOpener

if TYPE_CHECKING:
    from .definition import Flow


@dataclass(frozen=True)
class StageNode:
    """Run one Stage."""

    stage: Stage[Any, Any, Any]


@dataclass(frozen=True)
class EachNode:
    """Run `body` per selected item, one at a time, re-selecting after each."""

    select: Selector[Any, Any]
    body: Flow
    on_fail: Route
    on_hold: Route


@dataclass(frozen=True)
class DispatchNode:
    """Switch on the latest Event of the closed sum `on`; `cases` pairs each variant with what runs for it."""

    on: Any
    cases: tuple[tuple[type, Flow | Stop | Handoff], ...]


@dataclass(frozen=True)
class WithinNode:
    """Run `body` inside one opened agent workspace."""

    opener: WorkspaceOpener[Any]
    body: Flow


type Node = StageNode | EachNode | DispatchNode | WithinNode


@dataclass(frozen=True)
class ErrorRoute:
    """The exception types a Workflow turns into a route; anything else propagates."""

    errors: tuple[type[BaseException], ...]
    route: Stop | Handoff
