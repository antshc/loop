"""The program-counter interpreter: walks a Flow's nodes, moves the counter by route, and ends the scope at a terminal status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..facts import closed_sum
from ..nodes import DispatchNode, EachNode, Node, StageNode, WithinNode
from ..routes import Handoff, Next, NextItem, Reroute, Route, Stop
from ..stage import Item, Selector, Stage
from .attempt import run_stage
from .resolve import resolve
from .scope import Scope
from .status import Again, Outcome, ScopeStatus


@dataclass(frozen=True)
class _Step:
    """A node's result when its scope goes on: the route to take and the reason an ending route reports."""

    route: Route | Again
    reason: str | None = None


def interpret(nodes: tuple[Node, ...], scope: Scope) -> ScopeStatus:
    """Runs `nodes` in `scope` until the last one passes or a route ends the scope."""
    pc = 0
    while pc < len(nodes):
        step = _run_node(nodes[pc], scope)
        if isinstance(step, ScopeStatus):
            return step
        match step.route:
            case Next():
                pc += 1
            case Again():
                pass
            case NextItem():
                return ScopeStatus("next_item")
            case Stop(status=status, reason=reason):
                return ScopeStatus(status, reason or step.reason)
            case Handoff(stage=stage):
                return _hand_off(stage, scope, step.reason)
            case Reroute(variant=variant):
                return ScopeStatus("rerouted", step.reason, variant)
    return ScopeStatus("completed")


def _run_node(node: Node, scope: Scope) -> _Step | ScopeStatus:
    match node:
        case StageNode(stage):
            return _run_stage(stage, scope)
        case EachNode():
            return _each(node, scope)
        case DispatchNode():
            return _dispatch(node, scope)
        case WithinNode():
            return _within(node, scope)
    raise TypeError(f"{node!r} is not a Flow node")


def _run_stage(stage: Stage[Any, Any, Any], scope: Scope) -> _Step:
    outcome = _run_and_record(stage, scope)
    return _Step(outcome.route, outcome.reason)


def _run_and_record(stage: Stage[Any, Any, Any], scope: Scope) -> Outcome:
    """Runs the Stage and makes the Event it carries, pass, fail or hold, visible to later nodes."""
    outcome = run_stage(stage, scope)
    scope.record(outcome.event, stage.id)
    return outcome


def _hand_off(stage: Stage[Any, Any, Any], scope: Scope, reason: str | None) -> ScopeStatus:
    """Runs the hand-off Stage once, then ends the scope handed off; a hand-off that could not land fails the scope instead."""
    outcome = _run_and_record(stage, scope)
    while isinstance(outcome.route, Again):
        outcome = _run_and_record(stage, scope)
    if outcome.kind in ("failed", "held"):
        return ScopeStatus("failed", f"hand-off '{stage.id}' did not land: {outcome.reason}")
    if stage.run_records_ is not None and outcome.event is not None:
        scope.run.attributes.update(stage.run_records_(outcome.event, reason))
    return ScopeStatus("handed_off", reason)


def _each(node: EachNode, scope: Scope) -> _Step | ScopeStatus:
    """Runs the body per item, re-selecting after each; an item already run is never run again by this loop."""
    done: set[tuple[str, str]] = set()
    while (item := _next_item(node.select, scope, done)) is not None:
        if scope.run.stop is not None and scope.run.stop.requested():
            return ScopeStatus("interrupted")
        done.add((item.id, item.revision))
        status = _run_item(node, scope, item)
        match status.kind:
            case "interrupted" | "rerouted":
                return status
            case "failed" | "handed_off":
                return _Step(node.on_fail, status.reason)
            case "held":
                return _Step(node.on_hold, status.reason)
    return _Step(Next())


def _next_item(select: Selector[Any, Any], scope: Scope, done: set[tuple[str, str]]) -> Item[Any] | None:
    candidates = select.select(resolve(select.input, scope))
    return next((item for item in candidates if (item.id, item.revision) not in done), None)


def _run_item(node: EachNode, scope: Scope, item: Item[Any]) -> ScopeStatus:
    child = scope.child_item(node.body, node.select, item)
    with child:
        status = interpret(node.body.nodes, child)
        child.finish(status)
    scope.adopt(child)
    return status


def _within(node: WithinNode, scope: Scope) -> _Step | ScopeStatus:
    with node.opener.open(resolve(node.opener.input, scope)) as workspace:
        child = scope.child_within(node.opener, workspace)
        with child:
            status = interpret(node.body.nodes, child)
            child.finish(status)
    scope.adopt(child)
    return _continue(status)


def _dispatch(node: DispatchNode, scope: Scope) -> _Step | ScopeStatus:
    """Runs the case of the latest variant; a reroute abandons it for a sibling case with the same facts."""
    shape = closed_sum(node.on)
    assert shape is not None
    event = scope.latest(shape)
    assert event is not None
    variant, case = next((variant, case) for variant, case in node.cases if isinstance(event, variant))
    scope.event("factory.dispatch", variant=variant.__name__)
    cases = dict(node.cases)
    while True:
        if isinstance(case, Stop):
            return _Step(case)
        if isinstance(case, Handoff):
            return _hand_off(case.stage, scope, None)
        child = scope.child_case(case, variant)
        with child:
            status = interpret(case.nodes, child)
            child.finish(status)
        scope.adopt(child)
        if status.kind != "rerouted":
            return _continue(status)
        assert status.variant is not None
        variant, case = status.variant, cases[status.variant]
        scope.event("factory.reroute", to=variant.__name__)


def _continue(status: ScopeStatus) -> _Step | ScopeStatus:
    """A finished inner scope lets the outer one go on only when it completed."""
    return _Step(Next()) if status.kind == "completed" else status
