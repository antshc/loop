"""Build-time analysis of a definition: input resolution, definite production, scopes, routes and switches."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .event import DefinitionError, Event
from .facts import (
    Fact,
    FieldKind,
    InputField,
    closed_sum,
    event_shape,
    input_fields,
    merge,
    place_plain,
    producers_of,
)
from .nodes import DispatchNode, EachNode, ErrorRoute, Node, StageNode, WithinNode
from .routes import Handoff, Next, NextItem, Reroute, Rework, Route, Stop
from .stage import (
    Action,
    AgentExecution,
    HarnessCommits,
    NoRepository,
    Retain,
    Selector,
    Stage,
    WorkspaceOpener,
)
from .worktree import WorktreeGit


@dataclass(frozen=True)
class WiringRow:
    """Where one input field of one Stage, Selector or workspace opener comes from."""

    owner: str
    field: str
    kind: FieldKind
    source: str


@dataclass(frozen=True)
class Analysis:
    """What a valid definition derives: the service types it needs and its wiring table."""

    services: frozenset[type]
    rows: tuple[WiringRow, ...]

    def explain(self) -> str:
        """The wiring table as text: one row per input field, naming the upstream Stage that feeds it."""
        header = WiringRow("stage", "field", FieldKind.PLAIN, "source")
        rows = [header, *self.rows]
        widths = (
            max(len(row.owner) for row in rows),
            max(len(row.field) for row in rows),
        )
        lines = [
            f"{row.owner:<{widths[0]}}  {row.field:<{widths[1]}}  {row.source}".rstrip()
            for row in rows
        ]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _Case:
    """The dispatch case a node sits in: its sibling variants, its own variant and the reroutes found so far."""

    variants: frozenset[type]
    current: type
    reroutes: dict[type, dict[type, str]]


@dataclass(frozen=True)
class _Env:
    """What a node can see lexically: the context type, the enclosing loop items, the workspace and the dispatch case."""

    context: type | None
    items: tuple[type, ...] = ()
    workspace: bool = False
    case: _Case | None = None


@dataclass(frozen=True)
class _Exit:
    """The facts when control leaves a node, and whether control leaves it at all."""

    facts: tuple[Fact, ...]
    continues: bool = True


@dataclass
class _Walker:
    """One pass over a definition; `strict` adds the checks that need the whole upstream, which a loose fragment lacks."""

    strict: bool
    context: type | None
    stages: dict[str, Stage[Any, Any, Any]] = field(default_factory=dict)
    placed: dict[str, Stage[Any, Any, Any]] = field(default_factory=dict)
    handoffs: dict[str, Stage[Any, Any, Any]] = field(default_factory=dict)
    rows: list[WiringRow] = field(default_factory=list)
    services: set[type] = field(default_factory=set)
    histories: list[tuple[str, str, type[Event]]] = field(default_factory=list)

    def flow(self, nodes: Sequence[Node], facts: tuple[Fact, ...], env: _Env) -> _Exit:
        """Walks the nodes in order; a node that cannot continue leaves the rest unreachable."""
        continues = True
        for node in nodes:
            exit_ = self.node(node, facts, env)
            facts, continues = exit_.facts, continues and exit_.continues
        return _Exit(facts, continues)

    def node(self, node: Node, facts: tuple[Fact, ...], env: _Env) -> _Exit:
        """Walks one construct."""
        match node:
            case StageNode(stage):
                return self.stage(stage, facts, env)
            case EachNode():
                return self.each(node, facts, env)
            case DispatchNode():
                return self.dispatch(node, facts, env)
            case WithinNode():
                return self.within(node, facts, env)
        raise DefinitionError(f"{node!r} is not a Flow node")

    # ---- Stage -------------------------------------------------------------------------------------------

    def stage(
        self, stage: Stage[Any, Any, Any], facts: tuple[Fact, ...], env: _Env
    ) -> _Exit:
        """Checks one placed Stage and returns the facts after it."""
        owner = f"Stage '{stage.id}'"
        self.register_placement(stage, owner)
        self.check_declaration(stage, owner, with_routes=True)
        self.require_git_for_contract(stage)
        if self.strict:
            self.check_workspace(stage, owner, env)
            self.resolve_inputs(owner, stage.id, stage.input, stage.reads_, facts, env)
        for route in (stage.routes.passed, stage.routes.failed, stage.routes.held):
            self.check_route(owner, route, facts, env)
        return self.stage_exit(stage, facts)

    def register_placement(self, stage: Stage[Any, Any, Any], owner: str) -> None:
        """Rejects a second placement under the same identity."""
        if stage.id in self.placed or stage.id in self.handoffs:
            raise DefinitionError(
                f"{owner}: identity is used more than once; rename the second placement with .named(...)"
            )
        self.placed[stage.id] = stage

    def check_declaration(
        self, stage: Stage[Any, Any, Any], owner: str, *, with_routes: bool
    ) -> None:
        """The checks that need nothing beyond the Stage itself."""
        if stage.once_ is not None and stage.limit_ is not None:
            raise DefinitionError(
                f"{owner}: set either once or an at-most-N limit, not both"
            )
        if stage.once_ is not None and (
            stage.executor is None or isinstance(stage.executor, Action)
        ):
            kind = (
                "no executor"
                if stage.executor is None
                else "a deterministic action executor"
            )
            raise DefinitionError(
                f"{owner}: a once-per-key rule needs an agent or effect executor, but this Stage has {kind}"
            )
        if stage.limit_ is not None and stage.limit_.attempts < 1:
            raise DefinitionError(
                f"{owner}: the limit must allow at least 1 attempt, not {stage.limit_.attempts}"
            )
        if with_routes:
            self.check_rework(stage, owner)

    def require_git_for_contract(self, stage: Stage[Any, Any, Any]) -> None:
        """A repository contract is enforced through the Git service, so the definition needs it."""
        if not isinstance(stage.work, NoRepository):
            self.services.add(WorktreeGit)

    def check_rework(self, stage: Stage[Any, Any, Any], owner: str) -> None:
        """Rework is for failures of this Stage only, bounded, and never combined with a retained worktree."""
        routes = stage.routes
        reworks = [
            route
            for route in (routes.passed, routes.failed, routes.held)
            if isinstance(route, Rework)
        ]
        if isinstance(routes.held, Rework):
            raise DefinitionError(
                f"{owner}: a hold route cannot ask for rework; a hold waits for something outside"
            )
        if isinstance(routes.passed, Rework):
            raise DefinitionError(f"{owner}: a pass route cannot ask for rework")
        for route in reworks:
            if route.max_attempts < 1:
                raise DefinitionError(
                    f"{owner}: the rework bound must be at least 1, not {route.max_attempts}"
                )
            if not isinstance(route.then, (Stop, Handoff)):
                raise DefinitionError(
                    f"{owner}: rework ends in a stop or a hand-off, not {route.then!r}"
                )
        if (
            reworks
            and isinstance(stage.work, HarnessCommits)
            and isinstance(stage.work.on_fail, Retain)
        ):
            raise DefinitionError(
                f"{owner}: retains a failed attempt for inspection, so it cannot have a rework route"
            )

    def check_workspace(
        self, stage: Stage[Any, Any, Any], owner: str, env: _Env
    ) -> None:
        """An agent, a repository contract or the workspace itself needs an enclosing workspace scope."""
        if env.workspace:
            return
        if isinstance(stage.executor, AgentExecution):
            reason = "runs an agent"
        elif not isinstance(stage.work, NoRepository):
            reason = f"has the repository contract {type(stage.work).__name__}"
        else:
            return
        raise DefinitionError(
            f"{owner}: {reason} but is placed outside a workspace scope"
        )

    def stage_exit(self, stage: Stage[Any, Any, Any], facts: tuple[Fact, ...]) -> _Exit:
        """The facts after a Stage on every path that continues to the next node."""
        paths: list[tuple[Fact, ...]] = []
        if stage.when_ is not None:
            paths.append(facts)
        if isinstance(stage.routes.passed, Next):
            paths.append((*facts, Fact(_produced(stage), stage.id, True)))
        if isinstance(stage.routes.failed, Next) or isinstance(stage.routes.held, Next):
            paths.append((*facts, *_may_emit(stage)))
        return _Exit(merge(paths), True) if paths else _Exit(facts, False)

    # ---- routes ------------------------------------------------------------------------------------------

    def check_route(
        self, owner: str, route: Route, facts: tuple[Fact, ...], env: _Env
    ) -> None:
        """A route must make sense where it is written."""
        match route:
            case NextItem() if self.strict and not env.items:
                raise DefinitionError(
                    f"{owner}: a next-item route appears outside an each loop"
                )
            case Reroute() if self.strict:
                self.check_reroute(owner, route, env)
            case Handoff():
                self.check_handoff(owner, route, facts, env)
            case Rework(then=then):
                self.check_route(owner, then, facts, env)

    def check_reroute(self, owner: str, route: Reroute, env: _Env) -> None:
        """A reroute targets a sibling case of the dispatch it is written in, and records the edge for the cycle check."""
        case = env.case
        if case is None:
            raise DefinitionError(f"{owner}: a reroute appears outside a dispatch case")
        if route.variant not in case.variants:
            raise DefinitionError(
                f"{owner}: reroute to {route.variant.__name__}, which is not a sibling case of {case.current.__name__}"
            )
        case.reroutes.setdefault(case.current, {})[route.variant] = owner

    def check_handoff(
        self, owner: str, route: Handoff, facts: tuple[Fact, ...], env: _Env
    ) -> None:
        """A hand-off runs an effect or action Stage, which is checked where the hand-off is written."""
        stage = route.stage
        label = f"Stage '{stage.id}'" if isinstance(stage, Stage) else repr(stage)
        if not isinstance(stage, Stage):
            raise DefinitionError(f"{owner}: a hand-off runs a Stage, not {stage!r}")
        if isinstance(stage.executor, AgentExecution):
            raise DefinitionError(
                f"{owner}: hand-off Stage '{stage.id}' runs an agent; a hand-off runs an effect or action Stage"
            )
        self.register_handoff(stage, owner)
        self.check_declaration(stage, label, with_routes=False)
        self.require_git_for_contract(stage)
        if self.strict:
            self.check_workspace(stage, label, env)
            self.resolve_inputs(label, stage.id, stage.input, stage.reads_, facts, env)

    def register_handoff(self, stage: Stage[Any, Any, Any], owner: str) -> None:
        """A hand-off identity belongs to one Stage value, which any number of routes may share."""
        known = self.handoffs.get(stage.id)
        if stage.id in self.placed or (
            known is not None and known is not stage and known != stage
        ):
            raise DefinitionError(
                f"{owner}: hand-off Stage identity '{stage.id}' is already used by a different Stage"
            )
        self.handoffs[stage.id] = stage

    # ---- input resolution --------------------------------------------------------------------------------

    def resolve_inputs(
        self,
        owner: str,
        row_id: str,
        input_type: type,
        reads: tuple[tuple[type[Event], str], ...],
        facts: tuple[Fact, ...],
        env: _Env,
    ) -> None:
        """Resolves every field of an input, recording its wiring row; raises naming the owner, the field and the producers."""
        fields = input_fields(owner, input_type)
        bindings = dict(reads)
        for item in fields:
            kind = (
                place_plain(item, env.context, env.items)
                if item.kind is FieldKind.PLAIN
                else item.kind
            )
            source = self.resolve_field(owner, item, kind, bindings, facts, env)
            self.rows.append(WiringRow(row_id, item.name, kind, source))
        if not fields:
            self.rows.append(WiringRow(row_id, "-", FieldKind.PLAIN, "no input"))
        for event, producer in reads:
            if not any(
                item.kind in (FieldKind.EVENT, FieldKind.OPTIONAL)
                and event in item.shape
                for item in fields
            ):
                raise DefinitionError(
                    f"{owner}: reads {event.__name__} from '{producer}', but no input field reads {event.__name__}"
                )

    def resolve_field(
        self,
        owner: str,
        item: InputField,
        kind: FieldKind,
        bindings: dict[type[Event], str],
        facts: tuple[Fact, ...],
        env: _Env,
    ) -> str:
        """Resolves one field and returns the source text for the wiring table."""
        match kind:
            case FieldKind.EVENT | FieldKind.OPTIONAL:
                return self.resolve_event(owner, item, kind, bindings, facts)
            case FieldKind.ALL:
                producers = self.producers(f"{owner} field '{item.name}'", item, facts)
                return f"all {_names(item.shape)} from {', '.join(producers)}"
            case FieldKind.HISTORY:
                (event,) = item.shape
                self.histories.append((owner, item.name, event))
                return f"history {_names(item.shape)}"
            case FieldKind.WORKSPACE:
                if not env.workspace:
                    raise DefinitionError(
                        f"{owner} field '{item.name}': reads the agent workspace outside a workspace scope"
                    )
                return "workspace"
            case FieldKind.CONTEXT:
                return f"context {item.type_.__name__}"
            case FieldKind.ITEM:
                return f"item {item.type_.__name__}"
        self.services.add(item.type_)
        return f"service {item.type_.__name__}"

    def resolve_event(
        self,
        owner: str,
        item: InputField,
        kind: FieldKind,
        bindings: dict[type[Event], str],
        facts: tuple[Fact, ...],
    ) -> str:
        """Resolves an Event field: one named or unambiguous producer, definitely produced unless the field is optional."""
        label = f"{owner} field '{item.name}'"
        bound = next(
            (producer for event, producer in bindings.items() if event in item.shape),
            None,
        )
        producers = self.producers(label, item, facts)
        if bound is not None:
            self.check_binding(label, item, bound, producers)
            producers = (bound,)
        elif len(producers) > 1:
            raise DefinitionError(
                f"{label}: {_names(item.shape)} is produced by {_quoted(producers)}; name one with .reads({_first(item)}, from_=...)"
            )
        if kind is FieldKind.EVENT:
            self.require_definite(label, item, producers[0], facts)
        suffix = " (optional)" if kind is FieldKind.OPTIONAL else ""
        return f"event {_names(item.shape)} from {producers[0]}{suffix}"

    def producers(
        self, label: str, item: InputField, facts: tuple[Fact, ...]
    ) -> tuple[str, ...]:
        """The upstream producers of the field, raising with the field's label when there is none."""
        producers = tuple(
            dict.fromkeys(
                p
                for event in sorted(item.shape, key=_name)
                for p in producers_of(facts, event)
            )
        )
        if not producers:
            raise DefinitionError(
                f"{label}: no earlier Stage produces {_names(item.shape)}; place a producer before this Stage"
            )
        return producers

    def check_binding(
        self, label: str, item: InputField, bound: str, producers: tuple[str, ...]
    ) -> None:
        """A named producer must produce the Event and sit upstream."""
        if bound in producers:
            return
        stage = self.stages.get(bound)
        if stage is None:
            raise DefinitionError(
                f"{label}: producer '{bound}' is not upstream; no earlier Stage has that identity"
            )
        if not item.shape & (_produced(stage) | frozenset(stage.may_emit)):
            raise DefinitionError(
                f"{label}: producer '{bound}' does not produce {_names(item.shape)}"
            )
        raise DefinitionError(
            f"{label}: producer '{bound}' is not upstream of this Stage"
        )

    def require_definite(
        self, label: str, item: InputField, producer: str, facts: tuple[Fact, ...]
    ) -> None:
        """A non-optional Event must be produced on every path that reaches the reader."""
        if any(
            fact.producer == producer and fact.definite and fact.shape <= item.shape
            for fact in facts
        ):
            return
        stage = self.stages.get(producer)
        if stage is not None and stage.when_ is not None:
            why = "its condition may skip it"
        elif stage is not None and (
            isinstance(stage.routes.failed, Next) or isinstance(stage.routes.held, Next)
        ):
            why = "its fail or hold route continues without it"
        else:
            why = "it does not produce exactly that Event on every path"
        raise DefinitionError(
            f"{label}: no earlier Stage definitely produces {_names(item.shape)}; producer '{producer}' may not, because {why}; "
            f"read {_names(item.shape)} | None instead"
        )

    # ---- constructs --------------------------------------------------------------------------------------

    def each(self, node: EachNode, facts: tuple[Fact, ...], env: _Env) -> _Exit:
        """A loop: the body sees the item; afterwards its Events are only maybe produced."""
        if self.strict:
            self.resolve_selector(node.select, facts, env)
        for route in (node.on_fail, node.on_hold):
            self.check_loop_route(node.select, route, facts, env)
        body_env = replace(env, items=(*env.items, node.select.item))
        self.flow(node.body.nodes, facts, body_env)
        return _Exit((*facts, *_maybe_produced(node.body.nodes)))

    def resolve_selector(
        self, select: Selector[Any, Any], facts: tuple[Fact, ...], env: _Env
    ) -> None:
        """A Selector's input resolves like a Stage's."""
        self.resolve_inputs(
            f"Selector '{select.id}'", select.id, select.input, (), facts, env
        )

    def check_loop_route(
        self,
        select: Selector[Any, Any],
        route: Route,
        facts: tuple[Fact, ...],
        env: _Env,
    ) -> None:
        """A loop's own routes cannot rework; the rest follow the usual placement rules."""
        owner = f"Selector '{select.id}'"
        if isinstance(route, Rework):
            raise DefinitionError(
                f"{owner}: an each loop cannot rework; rework belongs to a Stage's own route"
            )
        self.check_route(owner, route, facts, env)

    def within(self, node: WithinNode, facts: tuple[Fact, ...], env: _Env) -> _Exit:
        """A workspace scope: its body sees the workspace; the workspace is gone after it."""
        if self.strict:
            self.resolve_opener(node.opener, facts, env)
        return self.flow(node.body.nodes, facts, replace(env, workspace=True))

    def resolve_opener(
        self, opener: WorkspaceOpener[Any], facts: tuple[Fact, ...], env: _Env
    ) -> None:
        """A workspace opener's input resolves like a Stage's."""
        self.resolve_inputs(
            f"Workspace opener '{opener.id}'", opener.id, opener.input, (), facts, env
        )

    def dispatch(self, node: DispatchNode, facts: tuple[Fact, ...], env: _Env) -> _Exit:
        """An exhaustive switch over a closed sum, each case seeing its variant as definitely produced."""
        name = getattr(node.on, "__name__", repr(node.on))
        variants = closed_sum(node.on)
        if variants is None:
            raise DefinitionError(
                f"dispatch on {name}: not a closed sum of at least two Events"
            )
        self.check_exhaustive(name, variants, node)
        if self.strict:
            self.check_switched_sum_produced(name, variants, facts)
        reroutes: dict[type, dict[type, str]] = {}
        exits: list[_Exit] = []
        for variant, case in node.cases:
            exits.append(self.case(variant, case, variants, reroutes, facts, env))
        if self.strict:
            _require_acyclic(name, reroutes)
        continuing = [exit_.facts for exit_ in exits if exit_.continues]
        return _Exit(merge(continuing)) if continuing else _Exit(facts, False)

    def check_exhaustive(
        self, name: str, variants: frozenset[type], node: DispatchNode
    ) -> None:
        """Every variant is covered exactly once and nothing else is."""
        keys = [variant for variant, _ in node.cases]
        duplicated = sorted({key.__name__ for key in keys if keys.count(key) > 1})
        missing = sorted(
            variant.__name__ for variant in variants if variant not in keys
        )
        extra = sorted(key.__name__ for key in keys if key not in variants)
        if duplicated or missing or extra:
            problems = [
                f"{label} {', '.join(names)}"
                for label, names in (
                    ("misses", missing),
                    ("duplicates", duplicated),
                    ("covers non-variants", extra),
                )
                if names
            ]
            raise DefinitionError(f"dispatch on {name}: " + "; ".join(problems))

    def check_switched_sum_produced(
        self, name: str, variants: frozenset[type], facts: tuple[Fact, ...]
    ) -> None:
        """Some earlier Stage must definitely produce a variant, or the switch has nothing to switch on."""
        if not any(fact.definite and fact.shape & variants for fact in facts):
            raise DefinitionError(
                f"dispatch on {name}: no earlier Stage definitely produces any variant of {name}"
            )

    def case(
        self,
        variant: type,
        case: Any,
        variants: frozenset[type],
        reroutes: dict[type, dict[type, str]],
        facts: tuple[Fact, ...],
        env: _Env,
    ) -> _Exit:
        """One case: a Flow runs with its variant produced; a stop or hand-off ends the scope."""
        seen = (
            *facts,
            *(
                Fact(frozenset({variant}), f.producer, True)
                for f in facts
                if f.definite and variant in f.shape
            ),
        )
        if isinstance(case, Handoff):
            self.check_handoff(f"dispatch case {variant.__name__}", case, seen, env)
            return _Exit(facts, False)
        if isinstance(case, Stop):
            return _Exit(facts, False)
        case_env = replace(env, case=_Case(variants, variant, reroutes))
        return self.flow(case.nodes, seen, case_env)


def _produced(stage: Stage[Any, Any, Any]) -> frozenset[type[Event]]:
    """The Events a Stage's pass may carry."""
    shape = event_shape(stage.produces)
    assert shape is not None
    return shape


def _may_emit(stage: Stage[Any, Any, Any]) -> tuple[Fact, ...]:
    """Facts for the Events a fail or hold of the Stage may carry."""
    return tuple(Fact(frozenset({event}), stage.id, False) for event in stage.may_emit)


def _stages_in(nodes: Iterable[Node]) -> Iterable[Stage[Any, Any, Any]]:
    """Every placed Stage under the nodes, depth first."""
    for node in nodes:
        match node:
            case StageNode(stage):
                yield stage
                yield from _hand_offs(
                    (stage.routes.passed, stage.routes.failed, stage.routes.held)
                )
            case EachNode(body=body):
                yield from _hand_offs((node.on_fail, node.on_hold))
                yield from _stages_in(body.nodes)
            case WithinNode(body=body):
                yield from _stages_in(body.nodes)
            case DispatchNode(cases=cases):
                for _, case in cases:
                    if isinstance(case, Handoff):
                        yield case.stage
                    elif not isinstance(case, Stop):
                        yield from _stages_in(case.nodes)


def _hand_offs(routes: Iterable[Route]) -> Iterable[Stage[Any, Any, Any]]:
    """The hand-off Stages the routes run; their Events reach the enclosing scope, so later Stages may read them."""
    for route in routes:
        route = route.then if isinstance(route, Rework) else route
        if isinstance(route, Handoff):
            yield route.stage


def _maybe_produced(nodes: Iterable[Node]) -> tuple[Fact, ...]:
    """Facts that every Event a body's Stages may produce is only maybe produced."""
    return tuple(
        fact
        for stage in _stages_in(nodes)
        for fact in (Fact(_produced(stage), stage.id, False), *_may_emit(stage))
    )


def _names(shape: frozenset[type[Event]]) -> str:
    """The Event names of a shape, sorted."""
    return " | ".join(sorted(_name(event) for event in shape))


def _name(event: type) -> str:
    """An Event class name."""
    return event.__name__


def _first(item: InputField) -> str:
    """The Event name to show in a `.reads(...)` hint."""
    return min(_name(event) for event in item.shape)


def _quoted(identities: Iterable[str]) -> str:
    """Identities in quotes, comma separated."""
    return ", ".join(f"'{identity}'" for identity in identities)


def _require_acyclic(name: str, reroutes: dict[type, dict[type, str]]) -> None:
    """Reroutes between sibling cases must not form a cycle."""
    finished: set[type] = set()

    def visit(variant: type, path: tuple[type, ...]) -> None:
        if variant in path:
            cycle = " -> ".join(
                _name(step) for step in (*path[path.index(variant) :], variant)
            )
            steps = (*path[path.index(variant) :], variant)
            owners = ", ".join(
                dict.fromkeys(
                    reroutes[source][target] for source, target in zip(steps, steps[1:])
                )
            )
            raise DefinitionError(
                f"dispatch on {name}: reroutes form a cycle {cycle} (via {owners})"
            )
        if variant in finished:
            return
        for target in reroutes.get(variant, {}):
            visit(target, (*path, variant))
        finished.add(variant)

    for variant in list(reroutes):
        visit(variant, ())


def analyze(
    nodes: Sequence[Node],
    *,
    strict: bool,
    context: type | None = None,
    error_routes: Sequence[ErrorRoute] = (),
    whole: bool = False,
) -> Analysis:
    """Validates a definition; `strict` resolves inputs against the Workflow, `whole` adds the checks that need every Stage."""
    walker = _Walker(strict, context)
    walker.stages = {stage.id: stage for stage in reversed(list(_stages_in(nodes)))}
    env = _Env(context)
    walker.flow(nodes, (), env)
    if strict:
        for error_route in error_routes:
            walker.check_route("Workflow error route", error_route.route, (), env)
    if strict and whole:
        _require_history_producers(walker)
    return Analysis(frozenset(walker.services), _distinct(walker.rows))


def _require_history_producers(walker: _Walker) -> None:
    """Every History[T] needs some Stage of the definition that produces T."""
    produced = {
        event
        for stage in walker.stages.values()
        for event in (*_produced(stage), *stage.may_emit)
    }
    for owner, name, event in walker.histories:
        if event not in produced:
            raise DefinitionError(
                f"{owner} field '{name}': no Stage of the definition produces {_name(event)} for its history"
            )


def _distinct(rows: Iterable[WiringRow]) -> tuple[WiringRow, ...]:
    """The first row for each owner and field; a hand-off Stage shared by many routes appears once."""
    first: dict[tuple[str, str], WiringRow] = {}
    for row in rows:
        first.setdefault((row.owner, row.field), row)
    return tuple(first.values())
