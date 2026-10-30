"""Flow and Workflow: immutable definitions built fluently, validated as each node is added."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Self

from .analysis import Analysis, analyze
from .event import DefinitionError
from .nodes import DispatchNode, EachNode, ErrorRoute, Node, StageNode, WithinNode
from .routes import Handoff, Route, Stop
from .stage import Selector, Stage, WorkspaceOpener

type CaseTarget = Flow | Stop | Handoff


@dataclass(frozen=True)
class Flow:
    """An ordered, immutable sequence of nodes; every method validates what it can see and returns a new Flow."""

    name: str
    nodes: tuple[Node, ...] = ()

    def __post_init__(self) -> None:
        """Rejects a blank name."""
        if not self.name.strip():
            raise DefinitionError("a Flow needs a name")

    def stage(self, stage: Stage[Any, Any, Any]) -> Self:
        """Appends a Stage."""
        if not isinstance(stage, Stage):
            raise DefinitionError(f"Flow '{self.name}': {stage!r} is not a Stage")
        return self._extended(StageNode(stage))

    def each[T](
        self, select: Selector[Any, T], body: Flow, *, on_fail: Route = Stop("failed"), on_hold: Route = Stop("held"),
    ) -> Self:
        """Runs `body` per selected item; Events produced inside are only maybe produced afterwards."""
        self._require_body(body, "each")
        return self._extended(EachNode(select, body, on_fail, on_hold))

    def dispatch(self, on: Any, cases: Mapping[type, CaseTarget] | Iterable[tuple[type, CaseTarget]]) -> Self:
        """Switches on the latest Event of the closed sum `on`; `cases` must cover every variant exactly once."""
        pairs = tuple(cases.items() if isinstance(cases, Mapping) else cases)
        for _, case in pairs:
            if isinstance(case, Flow):
                self._require_body(case, "dispatch")
        return self._extended(DispatchNode(on, pairs))

    def within(self, opener: WorkspaceOpener[Any], body: Flow) -> Self:
        """Runs `body` inside one opened agent workspace."""
        self._require_body(body, "within")
        return self._extended(WithinNode(opener, body))

    def then(self, fragment: Flow) -> Self:
        """Splices a Flow fragment's nodes here; sugar for reuse that adds no scope."""
        self._require_body(fragment, "then")
        return self._extended(*fragment.nodes)

    def _require_body(self, body: object, construct: str) -> None:
        """A body is a Flow fragment, never a whole Workflow."""
        if not isinstance(body, Flow) or isinstance(body, Workflow):
            raise DefinitionError(f"Flow '{self.name}': {construct} takes a Flow fragment, not {body!r}")

    def _extended(self, *nodes: Node) -> Self:
        """A copy with `nodes` appended, validated as far as it can be."""
        extended = replace(self, nodes=(*self.nodes, *nodes))
        extended._analysis(whole=False)
        return extended

    def _analysis(self, *, whole: bool) -> Analysis:
        """Validates this Flow on its own; context-free rules only."""
        return analyze(self.nodes, strict=False, whole=whole)


@dataclass(frozen=True)
class Workflow[C](Flow):
    """A named Flow over one work item of context type C; every structural mistake is rejected as it is built."""

    context_type: type | None = None
    error_routes: tuple[ErrorRoute, ...] = ()

    def context(self, type_: type[C]) -> Workflow[C]:
        """Declares the work-item context type Stages may read."""
        changed = replace(self, context_type=type_)
        changed._analysis(whole=False)
        return changed

    def on_error(self, errors: tuple[type[BaseException], ...], route: Stop | Handoff) -> Self:
        """The only exception types the runtime turns into a route; anything else propagates."""
        self._require_error_route(errors, route)
        changed = replace(self, error_routes=(*self.error_routes, ErrorRoute(tuple(errors), route)))
        changed._analysis(whole=False)
        return changed

    def _require_error_route(self, errors: tuple[type[BaseException], ...], route: object) -> None:
        """Error types are specific exception classes, listed once, and the route stops or hands off."""
        if not errors:
            raise DefinitionError(f"Workflow '{self.name}': on_error needs at least one exception type")
        for error in errors:
            if not (isinstance(error, type) and issubclass(error, BaseException)):
                raise DefinitionError(f"Workflow '{self.name}': {error!r} is not an exception type")
            if error in (Exception, BaseException):
                raise DefinitionError(f"Workflow '{self.name}': on_error cannot catch every exception; list specific types")
        known = {error for error_route in self.error_routes for error in error_route.errors}
        repeated = sorted(error.__name__ for error in errors if error in known)
        if repeated:
            raise DefinitionError(f"Workflow '{self.name}': {', '.join(repeated)} already has an error route")
        if not isinstance(route, (Stop, Handoff)):
            raise DefinitionError(f"Workflow '{self.name}': an error route stops or hands off, not {route!r}")

    def check(self) -> Self:
        """Whole-definition validation: unique identities, History producers, acyclic reroutes and exhaustive switches."""
        self._analysis(whole=True)
        return self

    def services(self) -> frozenset[type]:
        """The service types the definition needs, derived from every input dataclass."""
        return self._analysis(whole=False).services

    def explain(self) -> str:
        """The derived wiring table: for each Stage, which upstream Stage feeds each input field."""
        return self._analysis(whole=False).explain()

    def _analysis(self, *, whole: bool) -> Analysis:
        """Validates the Workflow with every input resolved against what is upstream of it."""
        return analyze(self.nodes, strict=True, context=self.context_type, error_routes=self.error_routes, whole=whole)
