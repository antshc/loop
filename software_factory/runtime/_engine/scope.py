"""The scope the interpreter walks in: the Events it holds, its enclosing items and workspace, and its lazily opened span."""

from __future__ import annotations

from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loop import AgentWorkspace

from ..definition import Workflow
from ..event import Event
from ..ledger import ScopeLog, WorkItemLog, WorkKey
from ..nodes import ErrorRoute
from ..spans import Attributes, SpanHandle, Spans
from ..stage import Item, Selector, WorkspaceOpener
from ..worktree import WorktreeGit
from .status import Recorded, ScopeStatus

if TYPE_CHECKING:
    from ..definition import Flow
    from ..run import Services, StopSignal


@dataclass(frozen=True)
class Run:
    """Everything one run shares: the definition, the work item, the services, where records and spans go and the attributes a hand-off adds to the closed run."""

    workflow: Workflow[Any]
    context: Any
    item: str
    epoch: int
    services: Services
    log: WorkItemLog
    spans: Spans
    stop: StopSignal | None
    attributes: dict[str, str | int | bool] = field(default_factory=dict)


def clean(attributes: dict[str, Any]) -> dict[str, str | int | bool]:
    """The attributes whose value is set; a span attribute is never None."""
    return {key: value for key, value in attributes.items() if value is not None}


class Scope:
    """One scope of a run: the run itself, a loop item, a workspace scope or a switch case."""

    def __init__(
        self, run: Run, parent: Scope | None, *, name: str, path: tuple[str, ...], revision: str, ledger: ScopeLog,
        attributes: Attributes, items: tuple[tuple[type, Any], ...] = (), workspace: AgentWorkspace | None = None,
        handle: SpanHandle | None = None,
    ) -> None:
        self.run = run
        self.parent = parent
        self.path = path
        self.revision = revision
        self.ledger = ledger
        self.items = items
        self.workspace = workspace
        self.events: list[Recorded] = []
        self._name = name
        self._attributes = attributes
        self._handle = handle
        self._stack = ExitStack()
        self._pending: list[tuple[str, dict[str, str | int | bool]]] = []
        self._pure_runs: dict[str, int] = {}

    @classmethod
    def root(cls, run: Run, revision: str, handle: SpanHandle) -> Scope:
        """The run's own scope; its span is the run span."""
        return cls(
            run, None, name=run.workflow.name, path=(), revision=revision, ledger=run.log.root_scope(revision),
            attributes={}, handle=handle,
        )

    # ---- children ----------------------------------------------------------------------------------------

    def child_item[T](self, body: Flow, select: Selector[Any, T], item: Item[T]) -> Scope:
        """The scope of one loop item."""
        attributes = {"factory.scope": body.name, "factory.item": item.id, "factory.revision": item.revision}
        return self._child(
            f"{body.name} {item.id}", (body.name, item.id), attributes, item.revision, item.value,
            items=(*self.items, (select.item, item.value)),
        )

    def child_within(self, opener: WorkspaceOpener[Any], workspace: AgentWorkspace) -> Scope:
        """The scope of one opened workspace."""
        return self._child(opener.id, (opener.id,), {"factory.scope": opener.id}, self.revision, None, workspace=workspace)

    def child_case(self, body: Flow, variant: type[Event]) -> Scope:
        """The scope of one switch case."""
        attributes = {"factory.scope": body.name, "factory.item": variant.__name__}
        return self._child(f"{body.name} {variant.__name__}", (body.name, variant.__name__), attributes, self.revision, None)

    def _child(
        self, name: str, path_part: tuple[str, ...], attributes: dict[str, str | int | bool], revision: str, item: Any,
        *, items: tuple[tuple[type, Any], ...] | None = None, workspace: AgentWorkspace | None = None,
    ) -> Scope:
        workspace = workspace or self.workspace
        path = (*self.path, *path_part)
        child = Scope(
            self.run, self, name=name, path=path, revision=revision, attributes=attributes,
            items=self.items if items is None else items, workspace=workspace,
            ledger=self.run.log.scope(self.ledger, path, item, revision, lambda: _head_of(self.run, workspace)),
        )
        return child

    # ---- Events ------------------------------------------------------------------------------------------

    def record(self, event: Event | None, producer: str) -> None:
        """Makes an Event visible to later nodes of this scope and its children."""
        if event is not None:
            self.events.append(Recorded(event, producer))

    def adopt(self, child: Scope) -> None:
        """Makes the Events a finished child scope recorded visible here."""
        self.events.extend(child.events)

    def latest(self, shape: frozenset[type[Event]], producer: str | None = None) -> Event | None:
        """The newest Event of this run in `shape`, optionally from one producer."""
        for recorded in reversed(self.visible()):
            if isinstance(recorded.event, tuple(shape)) and producer in (None, recorded.producer):
                return recorded.event
        return None

    def every(self, shape: frozenset[type[Event]]) -> tuple[Event, ...]:
        """Every Event of this run in `shape`, in order."""
        return tuple(recorded.event for recorded in self.visible() if isinstance(recorded.event, tuple(shape)))

    def visible(self) -> list[Recorded]:
        """The Events this scope sees, oldest first: the enclosing scopes' then its own."""
        inherited = self.parent.visible() if self.parent is not None else []
        return [*inherited, *self.events]

    # ---- context -----------------------------------------------------------------------------------------

    @property
    def item_types(self) -> tuple[type, ...]:
        """The declared types of the enclosing loop items, outermost first."""
        return tuple(type_ for type_, _ in self.items)

    def item_value(self, declared: type) -> Any:
        """The nearest enclosing loop item that satisfies `declared`."""
        for type_, value in reversed(self.items):
            if issubclass(type_, declared):
                return value
        raise LookupError(f"no enclosing loop item is a {declared.__name__}")

    def key(self, stage_id: str, revision: str | None) -> WorkKey:
        """The work key of a Stage placed in this scope."""
        item = "/".join((self.run.workflow.name, self.run.item, *self.path, stage_id))
        return WorkKey(item, self.revision if revision is None else f"{self.revision}+{revision}", self.run.epoch)

    @property
    def errors(self) -> tuple[type[BaseException], ...]:
        """Every exception type the Workflow turns into a route."""
        return tuple(error for route in self.run.workflow.error_routes for error in route.errors)

    def error_route(self, exception: BaseException) -> ErrorRoute:
        """The declared route for an exception that `errors` caught."""
        return next(route for route in self.run.workflow.error_routes if isinstance(exception, route.errors))

    def count_pure(self, stage_id: str) -> int:
        """Counts one more run of a check Stage and returns how many have run here."""
        self._pure_runs[stage_id] = self._pure_runs.get(stage_id, 0) + 1
        return self._pure_runs[stage_id]

    # ---- spans -------------------------------------------------------------------------------------------

    def open(self) -> None:
        """Opens this scope's span, and its enclosing scopes' first, unless it is already open."""
        if self._handle is not None:
            return
        if self.parent is not None:
            self.parent.open()
        self._handle = self._stack.enter_context(self.run.spans.open(self._name, self._attributes, role="code"))
        for name, attributes in self._pending:
            self._handle.event(name, **attributes)
        self._pending.clear()

    def event(self, name: str, *, quiet: bool = False, **attributes: Any) -> None:
        """A span event on this scope; a quiet one waits for the scope to open for another reason and is otherwise dropped."""
        values = clean(attributes)
        if self._handle is None:
            if quiet:
                self._pending.append((name, values))
                return
            self.open()
        self._handle.event(name, **values)

    def attempt_span(self, name: str, attributes: Attributes, *, role: str) -> AbstractContextManager[SpanHandle]:
        """Opens a Stage attempt's span under this scope, opening the scope if it is not yet."""
        self.open()
        return self.run.spans.open(name, attributes, role=role)

    def finish(self, status: ScopeStatus) -> None:
        """Reports how the scope ended and records its end; a scope that failed, held or handed off is always reported."""
        kind = "completed" if status.kind == "next_item" else status.kind
        if kind in ("failed", "held", "handed_off"):
            self.open()
        if self._handle is not None:
            self._handle.set(**clean({"factory.scope.status": kind}))
            if kind in ("failed", "handed_off"):
                self._handle.fail(status.reason or kind)
        self.ledger.close(kind)

    def __enter__(self) -> Scope:
        return self

    def __exit__(self, *exception: Any) -> bool | None:
        return self._stack.__exit__(*exception)


def _head_of(run: Run, workspace: AgentWorkspace | None) -> str | None:
    """The branch head of a workspace, when the run has a Git service and a workspace."""
    if workspace is None or WorktreeGit not in run.services.types():
        return None
    return run.services.get(WorktreeGit).head(workspace.path)
