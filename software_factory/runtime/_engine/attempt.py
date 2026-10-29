"""One Stage placement: resolve its input, skip or execute it, gate it, enforce its repository contract and pick its route."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from software_factory.agents import AgentError, Declined

from ..facts import event_shape
from ..event import DefinitionError, Event
from ..ledger import Attempt, AttemptLoop, WorkKey
from ..routes import Handoff, Next, Rework, Route
from ..spans import SpanHandle
from ..stage import (
    Action, AgentExecution, Effect, Evidence, Fail, HarnessCommits, Hold, Nothing, NoRepository, Pass, Stage, Verdict,
)
from ..worktree import WorktreeGit
from .contracts import Worktree, rolls_back, violation
from .resolve import resolve
from .scope import Scope, clean
from .status import Again, Outcome

NO_EVIDENCE = Evidence("", "", False)

_KINDS = {Pass: "passed", Fail: "failed", Hold: "held"}
_WORDS = {"passed": "pass", "failed": "fail", "held": "hold"}
_EXECUTORS = {AgentExecution: "agent", Action: "action", Effect: "effect", type(None): "none"}


def run_stage(stage: Stage[Any, Any, Any], scope: Scope) -> Outcome:
    """Runs one placement of `stage` in `scope` and returns how it ended and where control goes next."""
    stage_input = resolve(stage.input, scope, stage.reads_)
    if stage.when_ is not None and not stage.when_(stage_input):
        scope.event("factory.skipped", stage=stage.id)
        return Outcome("skipped", Next())
    if _is_check(stage):
        return _run_check(stage, scope, stage_input)
    return _Attempt.start(stage, scope, stage_input).run()


def _is_check(stage: Stage[Any, Any, Any]) -> bool:
    """A pure Stage: no executor or a deterministic action, and no worktree."""
    return isinstance(stage.executor, (Action, type(None))) and isinstance(stage.work, NoRepository)


def _run_check(stage: Stage[Any, Any, Any], scope: Scope, stage_input: Any) -> Outcome:
    """A check Stage opens no span; its outcome is a scope event and a buffered ledger record."""
    started = scope.count_pure(stage.id)
    error_route: Route | None = None
    try:
        reply = stage.executor.run(stage_input) if isinstance(stage.executor, Action) else Nothing()
        verdict = _judge(stage, stage_input, reply, NO_EVIDENCE)
    except scope.errors as exception:
        verdict, error_route = _declared_failure(scope, exception)
    kind, event, reason = _summary(verdict)
    scope.ledger.pure(stage.id, _WORDS[kind], event, reason)
    scope.event("factory.gate", quiet=kind == "passed", stage=stage.id, verdict=_WORDS[kind], reason=reason)
    return Outcome(kind, _route(stage, kind, started, scope.event, error_route), event, reason)


@dataclass
class _Attempt:
    """One executing attempt of a Stage: its worktree, its ledger entry and its span."""

    stage: Stage[Any, Any, Any]
    scope: Scope
    stage_input: Any
    worktree: Worktree | None
    loop: AttemptLoop
    attempt: Attempt
    key: WorkKey
    evidence: Evidence = NO_EVIDENCE
    commit: str | None = None
    error_route: Route | None = None
    span: SpanHandle | None = None

    @classmethod
    def start(cls, stage: Stage[Any, Any, Any], scope: Scope, stage_input: Any) -> _Attempt:
        """Records the attempt's intent before anything executes."""
        worktree = _worktree(stage, scope)
        revision = stage.revision_(stage_input) if stage.revision_ is not None else None
        key = scope.key(stage.id, revision)
        loop = scope.ledger.loop(key)
        base = worktree.git.head(worktree.path) if worktree else None
        status = worktree.git.status(worktree.path) if worktree else None
        return cls(stage, scope, stage_input, worktree, loop, loop.begin(base, status), key)

    def run(self) -> Outcome:
        """Executes, gates, enforces the contract, rolls back a rejected attempt and decides the route."""
        with self.scope.attempt_span(self.stage.id, self._attributes(), role=self._role()) as span:
            self.span = span
            verdict = self._attempt()
            self._roll_back(verdict)
            kind, event, reason = _summary(verdict)
            self.loop.finish(self.attempt, kind, reason=reason, commit=self.commit, event=event)
            span.set(**clean({"factory.stage.outcome": _WORDS[kind], "factory.stage.reason": reason}))
            if kind == "failed":
                span.fail(reason or "failed")
            route = _route(self.stage, kind, self.attempt.n, span.event, self.error_route)
        return Outcome(kind, route, event, reason)

    def _attempt(self) -> Verdict[Any]:
        try:
            reply = self._execute()
            self.loop.executed(self.attempt, reply)
            verdict = self._gate(reply)
        except AgentError as exception:
            return Fail(str(exception))
        except self.scope.errors as exception:
            verdict, self.error_route = _declared_failure(self.scope, exception)
            return verdict
        return self._enforce(verdict)

    def _execute(self) -> Any:
        executor, workspace = self.stage.executor, self.scope.workspace
        if isinstance(executor, AgentExecution):
            assert workspace is not None
            request = executor.request(self.stage_input, workspace.path)
            return executor.agent.run(workspace, request, session=executor.session(self.stage_input))
        if isinstance(executor, (Action, Effect)):
            return executor.run(self.stage_input)
        return Nothing()

    def _gate(self, reply: Any) -> Verdict[Any]:
        if self.worktree is not None:
            self.evidence = self.worktree.evidence(self.attempt.base or "", self.attempt.status or "")
        verdict = _judge(self.stage, self.stage_input, reply, self.evidence)
        _, _, reason = _summary(verdict)
        self._event("factory.gate", verdict=_WORDS[_KINDS[type(verdict)]], reason=reason)
        return verdict

    def _enforce(self, verdict: Verdict[Any]) -> Verdict[Any]:
        if not isinstance(verdict, Pass) or self.worktree is None:
            return verdict
        found = violation(self.stage.work, self.stage_input, verdict.event, self.evidence, self.worktree)
        self._event("factory.contract", ok=found is None, violation=found)
        if found is not None:
            return Fail(found)
        if isinstance(self.stage.work, HarnessCommits) and self.evidence.changed:
            return self._verify_then_commit(self.stage.work, verdict, self.worktree)
        return verdict

    def _verify_then_commit(self, work: HarnessCommits[Any, Any], verdict: Pass[Any], worktree: Worktree) -> Verdict[Any]:
        if work.verify is not None:
            failed = work.verify(worktree.path)
            self._event("factory.verify", ok=failed is None)
            if failed is not None:
                return failed
        message = work.message(self.stage_input, verdict.event)
        self.loop.committing(self.attempt, message, verdict.event)
        self.commit = worktree.git.commit(worktree.path, message)
        self._event("factory.commit", sha=self.commit)
        return verdict

    def _roll_back(self, verdict: Verdict[Any]) -> None:
        """A held attempt always leaves the repository as it found it; a failed one only when its contract restores."""
        if self.worktree is None or self.attempt.base is None:
            return
        if isinstance(verdict, Hold) or (isinstance(verdict, Fail) and rolls_back(self.stage.work)):
            self.worktree.git.restore(self.worktree.path, self.attempt.base)
            self._event("factory.restore", to=self.attempt.base)

    def _event(self, name: str, **attributes: Any) -> None:
        """A span event on this attempt's span."""
        assert self.span is not None
        self.span.event(name, **clean(attributes))

    def _attributes(self) -> dict[str, str | int | bool]:
        described = {f"factory.{name}": value for name, value in self.stage.describe(self.stage_input).items()}
        return {
            "factory.stage.id": self.stage.id, "factory.stage.attempt": self.attempt.n,
            "factory.stage.executor": _EXECUTORS[type(self.stage.executor)],
            "factory.stage.contract": type(self.stage.work).__name__, "factory.key.item": self.key.item,
            "factory.key.revision": self.key.revision, "factory.key.epoch": self.key.epoch, **described,
        }

    def _role(self) -> str:
        executor = self.stage.executor
        return executor.agent.role if isinstance(executor, AgentExecution) else "code"


def _worktree(stage: Stage[Any, Any, Any], scope: Scope) -> Worktree | None:
    """The workspace's worktree for a Stage with a repository contract."""
    if isinstance(stage.work, NoRepository):
        return None
    assert scope.workspace is not None
    return Worktree(scope.run.services.get(WorktreeGit), scope.workspace.path)


def _judge(stage: Stage[Any, Any, Any], stage_input: Any, reply: Any, evidence: Evidence) -> Verdict[Any]:
    """The trusted gate's verdict; an agent's reply never passes by itself, and a declined one fails."""
    if stage.judge is not None:
        verdict = stage.judge(stage_input, reply, evidence)
    elif isinstance(reply, Declined):
        verdict = Fail(reply.reason)
    else:
        verdict = Pass(reply)
    _require_declared(stage, verdict)
    return verdict


def _require_declared(stage: Stage[Any, Any, Any], verdict: Any) -> None:
    """A verdict is Pass, Fail or Hold, and its Event is one the Stage declares."""
    if not isinstance(verdict, (Pass, Fail, Hold)):
        raise DefinitionError(f"Stage '{stage.id}': the gate returned {verdict!r}, not a Pass, Fail or Hold")
    declared = tuple(event_shape(stage.produces) or ()) if isinstance(verdict, Pass) else stage.may_emit
    event = verdict.event
    if event is None and not isinstance(verdict, Pass):
        return
    if not isinstance(event, Event) or not isinstance(event, declared):
        word = "produces" if isinstance(verdict, Pass) else "may_emit"
        raise DefinitionError(f"Stage '{stage.id}': {event!r} is not an Event the Stage declares in {word}")


def _declared_failure(scope: Scope, exception: BaseException) -> tuple[Fail[Any], Route]:
    """A declared exception becomes a failure that takes the Workflow's error route."""
    return Fail(f"{type(exception).__name__}: {exception}"), scope.error_route(exception).route


def _summary(verdict: Verdict[Any]) -> tuple[str, Event | None, str | None]:
    """The outcome kind, the Event and the reason a verdict carries."""
    reason = None if isinstance(verdict, Pass) else verdict.reason
    return _KINDS[type(verdict)], verdict.event, reason


def _route(
    stage: Stage[Any, Any, Any], kind: str, started: int, emit: Callable[..., None], error_route: Route | None,
) -> Route | Again:
    """The route an outcome takes; a failure with rework budget left runs the same Stage again."""
    route = error_route or {"passed": stage.routes.passed, "failed": stage.routes.failed, "held": stage.routes.held}[kind]
    if isinstance(route, Rework):
        if started < route.max_attempts:
            emit("factory.rework", next_attempt=started + 1, budget=route.max_attempts)
            return Again()
        route = route.then
    if isinstance(route, Handoff):
        emit("factory.handoff", stage=route.stage.id)
    return route
