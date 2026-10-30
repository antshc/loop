"""Running a Workflow: the boundary that checks a definition and its services, then interprets it to a terminal status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from ._engine.interpret import interpret
from ._engine.scope import Run, Scope
from .definition import Workflow
from .event import DefinitionError
from .ledger import Ledger, WorkItemLocked, WorkItemLog
from .spans import NullSpans, Spans

_TERMINAL = ("completed", "idle", "held", "handed_off", "failed", "interrupted")
_EXIT_CODES = {"completed": 0, "idle": 0, "held": 0, "handed_off": 1, "failed": 1, "interrupted": 130}


@runtime_checkable
class WorkItem(Protocol):
    """A Workflow context that names its work item and the revision of what it is about."""

    id: str
    revision: str


class StopSignal(Protocol):
    """Asked between loop items whether the run should stop."""

    def requested(self) -> bool: ...


class Services:
    """Concrete service instances keyed by their exact type; built by an entry point or a test."""

    def __init__(self, *instances: object) -> None:
        self._instances: dict[type, object] = {}
        for instance in instances:
            if type(instance) in self._instances:
                raise ValueError(f"Services holds more than one {type(instance).__name__}")
            self._instances[type(instance)] = instance

    def replace(self, *instances: object) -> Services:
        """A copy in which each given instance takes the place of the one of its type."""
        replacements = {type(instance): instance for instance in instances}
        kept = (instance for type_, instance in self._instances.items() if type_ not in replacements)
        return Services(*kept, *replacements.values())

    def types(self) -> frozenset[type]:
        """The service types held."""
        return frozenset(self._instances)

    def get[T](self, type_: type[T]) -> T:
        """The instance of exactly `type_`."""
        try:
            return self._instances[type_]  # type: ignore[return-value]
        except KeyError:
            raise LookupError(f"no service of type {type_.__name__} was passed") from None


@dataclass(frozen=True)
class RunResult:
    """How one run of a work item ended."""

    status: Literal["completed", "idle", "held", "handed_off", "failed", "interrupted"]
    reason: str | None
    epoch: int

    @property
    def exit_code(self) -> int:
        """completed, idle and held are 0; handed off and failed are 1; interrupted is 130."""
        return _EXIT_CODES[self.status]


def run[C](
    workflow: Workflow[C], context: C, *, services: Services, ledger: Ledger, spans: Spans | None = None,
    stop: StopSignal | None = None,
) -> RunResult:
    """Runs one work item of a valid definition to a terminal status; a second process on the same item exits handed off without writing."""
    _require_runnable(workflow, context, services)
    assert isinstance(context, WorkItem)
    log = ledger.item(workflow.name, context.id)
    try:
        with log.lock():
            return _run_locked(workflow, context, services, log, spans or NullSpans(), stop)
    except WorkItemLocked:
        return RunResult("handed_off", f"work item '{context.id}' is locked by another run", log.last_epoch())


def _run_locked[C](
    workflow: Workflow[C], context: C, services: Services, log: WorkItemLog, spans: Spans, stop: StopSignal | None,
) -> RunResult:
    assert isinstance(context, WorkItem)
    opening = log.open_epoch()
    attributes = {"factory.workflow": workflow.name, "factory.work_item": context.id, "factory.epoch": opening.epoch}
    environment = Run(workflow, context, context.id, opening.epoch, services, log, opening, spans, stop)
    with spans.open(workflow.name, attributes, role="engineer") as handle:
        status = interpret(workflow.nodes, Scope.root(environment, context.revision, handle))
        result = RunResult(_terminal(status.kind), status.reason, opening.epoch)
        handle.set(**_run_attributes(result), **environment.attributes)
        if result.status in ("failed", "handed_off"):
            handle.fail(result.reason or result.status)
    log.close_run(result.status, result.reason, environment.attributes)
    return result


def _require_runnable(workflow: Workflow[Any], context: Any, services: Services) -> None:
    """Refuses a run before anything executes: an invalid definition, a missing service or a context that is no work item."""
    workflow.check()
    missing = sorted(type_.__name__ for type_ in workflow.services() - services.types())
    if missing:
        raise DefinitionError(f"Workflow '{workflow.name}' cannot run: Services is missing {', '.join(missing)}")
    if not isinstance(context, WorkItem):
        raise DefinitionError(f"Workflow '{workflow.name}' cannot run: {type(context).__name__} needs an id and a revision")


def _terminal(kind: str) -> Any:
    """The run status for a scope status; a next-item request that reached the top simply completes."""
    return kind if kind in _TERMINAL else "completed"


def _run_attributes(result: RunResult) -> dict[str, str | int | bool]:
    attributes: dict[str, str | int | bool] = {"factory.run.status": result.status}
    if result.reason is not None:
        attributes["factory.run.reason"] = result.reason
    return attributes
