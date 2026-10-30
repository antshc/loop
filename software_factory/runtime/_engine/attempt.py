"""One Stage placement: resolve its input, skip or execute it, gate it, enforce its repository contract and pick its route."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from software_factory.agents import AgentError, Declined, Json

from ..facts import event_shape
from ..event import DefinitionError, Event
from ..ledger import Attempt, AttemptLoop, AttemptState, WorkKey
from ..routes import Handoff, Next, Rework, Route
from ..spans import SpanHandle
from ..stage import (
    Action, AgentCommits, AgentExecution, Effect, Evidence, Fail, HarnessCommits, Hold, Nothing, NoRepository, Pass,
    Retain, Stage, Verdict,
)
from ..worktree import WorktreeGit
from .codec import decode, decode_stored, is_tagged
from .contracts import Worktree, rolls_back, violation
from .resolve import resolve
from .scope import Scope, clean
from .status import Again, Outcome

NO_EVIDENCE = Evidence("", "", False)

UNVERIFIED = "Unverified edits or unexpected commits retained in recovery worktree"
INTERRUPTED = "the attempt was interrupted before the agent replied"
COMMIT_GONE = "the accepted commit is no longer on the branch"

_KINDS = {Pass: "passed", Fail: "failed", Hold: "held"}
_WORDS = {"passed": "pass", "failed": "fail", "held": "hold"}
_KIND_OF_WORD = {word: kind for kind, word in _WORDS.items()}
_EXECUTORS = {AgentExecution: "agent", Action: "action", Effect: "effect", type(None): "none"}


def run_stage(stage: Stage[Any, Any, Any], scope: Scope, *, handoff: bool = False) -> Outcome:
    """Runs one placement of `stage` in `scope` and returns how it ended and where control goes next; a hand-off lands without the resume check."""
    stage_input = resolve(stage.input, scope, stage.reads_)
    if stage.when_ is not None and not stage.when_(stage_input):
        scope.event("factory.skipped", stage=stage.id)
        return Outcome("skipped", Next())
    if _is_check(stage):
        return _run_check(stage, scope, stage_input)
    return _Attempt.place(stage, scope, stage_input, handoff).run()


def _budget(stage: Stage[Any, Any, Any], stage_input: Any) -> dict[str, str] | None:
    """The once-per-key or at-most-N rule the placement counts against across runs, keyed by the Stage and the key it derives."""
    if stage.once_ is not None:
        return {"stage": stage.id, "per": stage.once_(stage_input)}
    if stage.limit_ is not None:
        return {"stage": stage.id, "per": stage.limit_.per(stage_input)}
    return None


def _stored_event(stage: Stage[Any, Any, Any], stored: Json) -> Event | None:
    """The Event a record carries, rebuilt as the Stage-declared Event its tag names."""
    if stored is None:
        return None
    declared = (*(event_shape(stage.produces) or ()), *stage.may_emit)
    for event in declared:
        if is_tagged(stored, event):
            return decode(stored, event)
    raise LookupError(f"Stage '{stage.id}' stored an Event it does not declare: {stored!r}")


def _recorded_error_route(scope: Scope, name: str | None) -> Route | None:
    """The route a recorded declared-exception failure took."""
    route = scope.error_route_named(name) if name else None
    return None if route is None else route.route


def _is_check(stage: Stage[Any, Any, Any]) -> bool:
    """A pure Stage: no executor or a deterministic action, and no worktree."""
    return isinstance(stage.executor, (Action, type(None))) and isinstance(stage.work, NoRepository)


def _run_check(stage: Stage[Any, Any, Any], scope: Scope, stage_input: Any) -> Outcome:
    """A check Stage opens no span; its outcome is a scope event and a buffered ledger record, replayed when the epoch already ran it."""
    started = scope.count_pure(stage.id)
    revision = scope.key(stage.id, stage.revision_(stage_input) if stage.revision_ is not None else None).revision
    recorded = scope.recorded_pure(stage.id, revision)
    if recorded is not None:
        return _replay_check(stage, scope, recorded, started)
    error_route: Route | None = None
    error: str | None = None
    try:
        reply = stage.executor.run(stage_input) if isinstance(stage.executor, Action) else Nothing()
        verdict = _judge(stage, stage_input, reply, NO_EVIDENCE)
    except scope.errors as exception:
        verdict, error_route, error = _declared_failure(scope, exception)
    kind, event, reason = _summary(verdict)
    scope.ledger.pure(stage.id, revision, _WORDS[kind], event, reason, error)
    scope.event("factory.gate", quiet=kind == "passed", stage=stage.id, verdict=_WORDS[kind], reason=reason)
    return Outcome(kind, _route(stage, kind, started, scope.event, error_route), event, reason)


def _replay_check(stage: Stage[Any, Any, Any], scope: Scope, recorded: dict[str, Any], started: int) -> Outcome:
    """A check Stage's recorded outcome, verdict included, without evaluating it again."""
    kind = _KIND_OF_WORD[recorded["outcome"]]
    scope.event("factory.replay", quiet=True, stage=stage.id, outcome=recorded["outcome"])
    route = _route(stage, kind, started, scope.event, _recorded_error_route(scope, recorded.get("error")))
    return Outcome(kind, route, _stored_event(stage, recorded["event"]), recorded["reason"])


@dataclass
class _Attempt:
    """One placement of an executing Stage: its worktree, its ledger entries and its span; it replays, reconciles or starts the attempt the ledger calls for."""

    stage: Stage[Any, Any, Any]
    scope: Scope
    stage_input: Any
    worktree: Worktree | None
    loop: AttemptLoop
    key: WorkKey
    budget: dict[str, str] | None
    handoff: bool
    attempt: Attempt = Attempt(0, None, None)
    evidence: Evidence = NO_EVIDENCE
    commit: str | None = None
    error_route: Route | None = None
    error_name: str | None = None
    keep_tree: bool = False
    force_final: bool = False
    span: SpanHandle | None = None

    @classmethod
    def place(cls, stage: Stage[Any, Any, Any], scope: Scope, stage_input: Any, handoff: bool) -> _Attempt:
        """The placement's worktree and the ledger loop of its work key."""
        revision = stage.revision_(stage_input) if stage.revision_ is not None else None
        key = scope.key(stage.id, revision)
        budget = _budget(stage, stage_input)
        return cls(stage, scope, stage_input, _worktree(stage, scope), scope.ledger.loop(key, budget), key, budget, handoff)

    def run(self) -> Outcome:
        """Replays what the work key already finished, reconciles an interrupted attempt, else starts a new one."""
        settled = self._replayed_pass_of_an_earlier_run()
        if settled is not None:
            return settled
        last = self.loop.last()
        if last is not None and last.settled:
            return self._replay(last)
        if last is not None and last.interrupted:
            reconciled = self._reconcile(last)
            if reconciled is not None:
                return reconciled
        return self._fresh()

    # ---- replay ------------------------------------------------------------------------------------------

    def _replayed_pass_of_an_earlier_run(self) -> Outcome | None:
        """A once-per-key Stage's pass from any other run or epoch, replayed and recorded in this epoch."""
        if self.stage.once_ is None or self.budget is None:
            return None
        passed = self.scope.run.log.passed_under(self.budget)
        if passed is None or passed["key"] == self.key.as_json():
            return None
        event = _stored_event(self.stage, passed["event"])
        self.scope.ledger.replayed(self.key, event)
        self.scope.event("factory.replay", stage=self.stage.id, outcome="pass")
        if self._commit_gone(passed.get("commit")):
            return self._refused(COMMIT_GONE)
        return Outcome("passed", _route(self.stage, "passed", 0, self.scope.event, None), event)

    def _replay(self, last: AttemptState) -> Outcome:
        """The outcome the work key already recorded, without running anything."""
        self.scope.event("factory.replay", quiet=last.status == "passed", stage=self.stage.id, outcome=_WORDS[last.status])
        event = _stored_event(self.stage, last.event)
        if last.status == "passed":
            if self._commit_gone(last.commit):
                return self._refused(COMMIT_GONE)
            return Outcome("passed", _route(self.stage, "passed", last.n, self.scope.event, None), event)
        route = _route(
            self.stage, last.status, last.n, self.scope.event, _recorded_error_route(self.scope, last.error), final=True,
        )
        return Outcome(last.status, route, event, last.reason)  # type: ignore[arg-type]

    def _commit_gone(self, commit: str | None) -> bool:
        """Whether an accepted commit is no longer an ancestor of the branch head."""
        return commit is not None and self.worktree is not None and not self.worktree.git.is_ancestor(self.worktree.path, commit)

    def _refused(self, reason: str) -> Outcome:
        """A failure that never reruns the Stage: its route's rework, if any, is skipped."""
        route = _route(self.stage, "failed", 0, self.scope.event, None, final=True)
        return Outcome("failed", route, None, reason)

    # ---- a new attempt -----------------------------------------------------------------------------------

    def _fresh(self) -> Outcome:
        exhausted = self._exhausted()
        if exhausted is not None:
            return exhausted
        unverified = self._resume_violation()
        if unverified is not None:
            self.loop.refuse(unverified)
            return self._refused(unverified)
        base = self.worktree.git.head(self.worktree.path) if self.worktree else None
        status = self.worktree.git.status(self.worktree.path) if self.worktree else None
        self.attempt = self.loop.begin(base, status)
        outcome = self._in_span(self._executed)
        assert outcome is not None
        return outcome

    def _exhausted(self) -> Outcome | None:
        """A failure when the at-most-N cap on started attempts for this key is already reached."""
        limit = self.stage.limit_
        if limit is None or self.budget is None:
            return None
        started = self.scope.run.log.started_under(self.budget)
        if started < limit.attempts:
            return None
        self.scope.event("factory.limit", stage=self.stage.id, key=self.budget["per"], started=started)
        reason = f"Stage '{self.stage.id}' has already started {started} of its {limit.attempts} attempts for '{self.budget['per']}'"
        return self._refused(reason)

    def _resume_violation(self) -> str | None:
        """Before the first new attempt of a resumed workspace scope: the reason the tree is dirty or the head is not the last recorded commit, else None."""
        owner = None if self.handoff else self.scope.resume_owner()
        if owner is None or owner.workspace is None or WorktreeGit not in self.scope.run.services.types():
            return None
        git = self.scope.run.services.get(WorktreeGit)
        path = owner.workspace.path
        if git.is_clean(path) and git.head(path) == owner.last_head:
            self.scope.mark_resume_checked()
            return None
        self.scope.event("factory.resume", ok=False, stage=self.stage.id)
        return UNVERIFIED

    def _executed(self) -> Verdict[Any]:
        verdict = self._guard(lambda: self._after_reply(self._execute()))
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

    def _after_reply(self, reply: Any) -> Verdict[Any]:
        self.loop.executed(self.attempt, reply, self._tree())
        return self._gate(reply)

    def _tree(self) -> str | None:
        return self.worktree.git.tree_id(self.worktree.path) if self.worktree else None

    def _guard[T](self, judge: Callable[[], T]) -> T | Fail[Any]:
        """Runs `judge`; an agent failure or a declared exception becomes a failed verdict."""
        try:
            return judge()
        except AgentError as exception:
            return Fail(str(exception))
        except self.scope.errors as exception:
            verdict, self.error_route, self.error_name = _declared_failure(self.scope, exception)
            return verdict

    # ---- reconciling an interrupted attempt --------------------------------------------------------------

    def _reconcile(self, last: AttemptState) -> Outcome | None:
        """Settles an attempt that started and never recorded an outcome; None when the Stage is simply to run again."""
        self.attempt = Attempt(last.n, last.base, last.porcelain)
        return self._in_span(lambda: self._interrupted(last))

    def _interrupted(self, last: AttemptState) -> Verdict[Any] | None:
        self._event("factory.reconcile", status=last.status)
        match last.status:
            case "running":
                return self._interrupted_before_reply()
            case "executed":
                return self._interrupted_after_reply(last)
        return self._interrupted_during_commit(last)

    def _interrupted_before_reply(self) -> Verdict[Any] | None:
        """An effect is adopted when its target shows it done; otherwise the attempt counts, the contract restores, or a retained tree must be at its base."""
        executor, work = self.stage.executor, self.stage.work
        if isinstance(executor, Effect):
            return self._adopt_or_rerun(executor)
        if isinstance(work, HarnessCommits) and isinstance(work.on_fail, Retain):
            return None if self._clean_at_base() else self._unverified()
        return Fail(INTERRUPTED)

    def _adopt_or_rerun(self, executor: Effect[Any, Any]) -> Verdict[Any] | None:
        verdict = self._guard(lambda: self._adopted(executor))
        return None if verdict is None else self._enforce(verdict)

    def _adopted(self, executor: Effect[Any, Any]) -> Verdict[Any] | None:
        done = executor.reconcile(self.stage_input)
        return None if done is None else self._after_reply(done)

    def _clean_at_base(self) -> bool:
        assert self.worktree is not None
        return self.worktree.git.is_clean(self.worktree.path) and self.worktree.git.head(self.worktree.path) == self.attempt.base

    def _interrupted_after_reply(self, last: AttemptState) -> Verdict[Any] | None:
        """The stored reply is gated again, without the agent, while the worktree content is the one the reply left."""
        try:
            reply = decode_stored(last.reply)
        except LookupError:
            executor = self.stage.executor
            return self._adopt_or_rerun(executor) if isinstance(executor, Effect) else self._unverified()
        if self.worktree is not None and self._tree() != last.tree:
            return self._unverified()
        return self._enforce(self._guard(lambda: self._gate(reply)))

    def _interrupted_during_commit(self, last: AttemptState) -> Verdict[Any]:
        """A commit the runtime was making: adopted when it is the intended one, redone when nothing moved and the work is restorable."""
        work, worktree = self.stage.work, self.worktree
        if worktree is None or not isinstance(work, HarnessCommits):
            return self._unrecognized("the commit belongs to no runtime-committing attempt")
        event = _stored_event(self.stage, last.event)
        assert event is not None
        if worktree.git.head(worktree.path) != last.base:
            return self._adopt_commit(worktree, last, event)
        if isinstance(work.on_fail, Retain) or self._tree() != last.tree:
            return self._unverified()
        return self._verify_then_commit(work, Pass(event), worktree, last.message)

    def _adopt_commit(self, worktree: Worktree, last: AttemptState, event: Event) -> Verdict[Any]:
        git, path = worktree.git, worktree.path
        commits = git.commits_since(path, last.base or "")
        if git.parents(path) == (last.base,) and commits and commits[-1].subject == last.message and git.is_clean(path):
            self.commit = git.head(path)
            self._event("factory.adopt", sha=self.commit)
            return Pass(event)
        return self._unrecognized(f"head {git.head(path)} is not the attempt's intended commit")

    def _unverified(self) -> Fail[Any]:
        """The attempt cannot be verified: nothing is staged, restored or committed, and the failure is final so a person looks."""
        self.keep_tree, self.force_final = True, True
        return Fail(UNVERIFIED)

    def _unrecognized(self, why: str) -> Fail[Any]:
        self.keep_tree, self.force_final = True, True
        return Fail(f"unrecognized commit: {why}")

    # ---- gate, contract, outcome -------------------------------------------------------------------------

    def _in_span(self, decide: Callable[[], Verdict[Any] | None]) -> Outcome | None:
        """Decides the attempt's verdict inside its span, rolls back, records the outcome and picks the route; None when `decide` says to run again."""
        with self.scope.attempt_span(self.stage.id, self._attributes(), role=self._role()) as span:
            self.span = span
            verdict = decide()
            if verdict is None:
                return None
            self._roll_back(verdict)
            kind, event, reason = _summary(verdict)
            route = _route(self.stage, kind, self.attempt.n, span.event, self.error_route, final=self.force_final)
            self.loop.finish(
                self.attempt, kind, reason=reason, commit=self.commit, event=event, final=not isinstance(route, Again),
                error=self.error_name,
            )
            if kind == "passed" and self.commit is not None:
                self.scope.advance_head(self.commit)
            span.set(**clean({"factory.stage.outcome": _WORDS[kind], "factory.stage.reason": reason}))
            if kind == "failed":
                span.fail(reason or "failed")
        return Outcome(kind, route, event, reason)

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
        if isinstance(self.stage.work, AgentCommits):
            self.commit = self.evidence.head
        if isinstance(self.stage.work, HarnessCommits) and self.evidence.changed:
            return self._verify_then_commit(self.stage.work, verdict, self.worktree)
        return verdict

    def _verify_then_commit(
        self, work: HarnessCommits[Any, Any], verdict: Pass[Any], worktree: Worktree, message: str | None = None,
    ) -> Verdict[Any]:
        if work.verify is not None:
            failed = work.verify(worktree.path)
            self._event("factory.verify", ok=failed is None)
            if failed is not None:
                return failed
        message = message if message is not None else work.message(self.stage_input, verdict.event)
        self.loop.committing(self.attempt, message, verdict.event)
        self.commit = worktree.git.commit(worktree.path, message)
        self._event("factory.commit", sha=self.commit)
        return verdict

    def _roll_back(self, verdict: Verdict[Any]) -> None:
        """A held attempt always leaves the repository as it found it; a failed one only when its contract restores; an unverified one is left for a person."""
        if self.worktree is None or self.attempt.base is None or self.keep_tree:
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


def _declared_failure(scope: Scope, exception: BaseException) -> tuple[Fail[Any], Route, str]:
    """A declared exception becomes a failure that takes the Workflow's error route; the exception's name is recorded to replay that route."""
    return Fail(f"{type(exception).__name__}: {exception}"), scope.error_route(exception).route, type(exception).__name__


def _summary(verdict: Verdict[Any]) -> tuple[str, Event | None, str | None]:
    """The outcome kind, the Event and the reason a verdict carries."""
    reason = None if isinstance(verdict, Pass) else verdict.reason
    return _KINDS[type(verdict)], verdict.event, reason


def _route(
    stage: Stage[Any, Any, Any], kind: str, started: int, emit: Callable[..., None], error_route: Route | None,
    *, final: bool = False,
) -> Route | Again:
    """The route an outcome takes; a failure with rework budget left runs the same Stage again unless the outcome is final."""
    route = error_route or {"passed": stage.routes.passed, "failed": stage.routes.failed, "held": stage.routes.held}[kind]
    if isinstance(route, Rework):
        if started < route.max_attempts and not final:
            emit("factory.rework", next_attempt=started + 1, budget=route.max_attempts)
            return Again()
        route = route.then
    if isinstance(route, Handoff):
        emit("factory.handoff", stage=route.stage.id)
    return route
