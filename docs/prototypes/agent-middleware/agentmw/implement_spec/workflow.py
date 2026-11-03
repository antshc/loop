"""implement_spec's sequence over the role pipelines: the Workflow owns order, rework and hand-offs."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..context import AgentRequest
from ..errors import AgentDeclined, AgentError
from ..host import AgentHost, AgentRun
from ..integrations import WorktreeGit
from . import templates
from .messages import Delivered, Proposed, Refined, Reviewed
from .policy import HITL, SOURCE, review_blockers, ticket_subject_prefix
from .tracker import PullRequest, Ticket, Tracker

logger = logging.getLogger("agentmw.implement_spec")

DEFAULT_REWORK = 2


@dataclass(frozen=True)
class SpecRoute:
    """The Spec's routing: its Initiative, branches, read-only checkout and feature worktree."""

    spec: int
    initiative: str
    base_branch: str
    feature_branch: str
    checkout: str
    worktree: str

    def metadata(self, path: str, **extra: str) -> dict[str, str]:
        """The request metadata of a call that runs in `path`."""
        return {
            "spec": str(self.spec),
            "initiative": self.initiative,
            "base_branch": self.base_branch,
            "feature_branch": self.feature_branch,
            "worktree": path,
            **extra,
        }


@dataclass(frozen=True)
class Outcome:
    """How the Spec's run ended: `ready`, `draft`, `handed_off`, `failed` or `idle`."""

    status: str
    reason: str = ""
    pull_request: str | None = None


class ImplementSpec:
    """One Spec, end to end, in one run scope of `host`."""

    def __init__(
        self,
        host: AgentHost,
        tracker: Tracker,
        git: WorktreeGit,
        *,
        max_failures: int = DEFAULT_REWORK,
    ) -> None:
        self._host = host
        self._tracker = tracker
        self._git = git
        self._max_failures = max_failures

    async def run(self, route: SpecRoute) -> Outcome:
        """Plans when the Spec has no Tickets, delivers them, then refines, reviews and finalizes."""
        async with self._host.run_scope() as run:
            spec_run = _SpecRun(run, route, self._tracker, self._git)
            return await spec_run.execute(self._max_failures)


class _SpecRun:
    def __init__(
        self, run: AgentRun, route: SpecRoute, tracker: Tracker, git: WorktreeGit
    ) -> None:
        self._run = run
        self._route = route
        self._tracker = tracker
        self._git = git

    async def execute(self, max_failures: int) -> Outcome:
        spec = await self._tracker.spec(self._route.spec)
        if not spec.tickets and not await self._plan():
            return Outcome("handed_off", "the plan needs a person")
        escalated = await self._deliver(max_failures)
        pull_request = await self._publish()
        undelivered = await self._undelivered(escalated)
        if undelivered is not None:
            return Outcome("failed", undelivered, _url(pull_request))
        if pull_request is None:
            return Outcome("idle", "nothing was committed")
        await self._comment(f"Tickets delivered in {pull_request.url}")
        await self._chore()
        head = await self._git.head(self._route.worktree)
        review = await self._review(head)
        return await self._finalize(pull_request, head, review)

    async def _plan(self) -> bool:
        """plan-spec, then create-tickets; any agent failure hands the Spec to a person."""
        request = AgentRequest(
            "planner",
            templates.PLANNER,
            Proposed,
            metadata=self._route.metadata(self._route.checkout),
        )
        try:
            proposed = await self._run.call(request)
        except AgentError as exception:
            await self._hand_to_human(_reason(exception))
            return False
        numbers = await self._tracker.create_tickets(self._route.spec, proposed.tickets)
        await self._comment(f"plan: {', '.join(f'#{n}' for n in numbers)}")
        return True

    async def _deliver(self, max_failures: int) -> list[int]:
        """Each READY Ticket in turn; the escalated ones are returned, the rest closed."""
        escalated: list[int] = []
        while (ticket := await self._next_ready(escalated)) is not None:
            delivered = await self._implement(ticket, max_failures)
            if delivered is None:
                escalated.append(ticket.number)
                continue
            await self._tracker.close(
                ticket.number,
                f"Delivered in {delivered.commit}.\n\n{delivered.summary}\n\n{delivered.verification}",
            )
        return escalated

    async def _next_ready(self, skipped: list[int]) -> Ticket | None:
        spec = await self._tracker.spec(self._route.spec)
        ready = [t for t in spec.ready_tickets(HITL) if t.number not in skipped]
        return ready[0] if ready else None

    async def _implement(self, ticket: Ticket, max_failures: int) -> Delivered | None:
        """implement-ticket with `rework(max_attempts, then=escalate)`: a fresh call per attempt."""
        initiative = self._route.initiative
        request = AgentRequest(
            "developer",
            templates.DEVELOPER,
            Delivered,
            metadata=self._route.metadata(
                self._route.worktree,
                ticket=str(ticket.number),
                identifier=f"{initiative}|{ticket.number}",
                commit_prefix=ticket_subject_prefix(initiative, ticket.number),
            ),
        )
        reason = "the Ticket was rejected after repeated attempts"
        for attempt in range(1, max_failures + 1):
            try:
                return await self._run.call(request)
            except AgentError as exception:
                reason = _reason(exception)
                logger.warning(
                    "Ticket #%s attempt %d/%d failed: %s",
                    ticket.number,
                    attempt,
                    max_failures,
                    reason,
                )
        await self._escalate(ticket.number, reason)
        return None

    async def _publish(self) -> PullRequest | None:
        """The draft pull request of the feature branch, once it has commits."""
        if not await self._git.branch_commits(self._route.worktree):
            return None
        spec = await self._tracker.spec(self._route.spec)
        return await self._tracker.publish_draft(self._route.feature_branch, spec.title)

    async def _undelivered(self, escalated: list[int]) -> str | None:
        """require-delivered: why the delivery is incomplete, or None."""
        if escalated:
            return (
                f"Tickets were escalated to a person in this run: {_numbers(escalated)}"
            )
        spec = await self._tracker.spec(self._route.spec)
        remaining = [ticket.number for ticket in spec.open_tickets()]
        if remaining:
            return f"Tickets remain undelivered: {_numbers(remaining)}"
        return None

    async def _chore(self) -> Refined | None:
        """refine-chore, best effort: any failure is logged and the delivery stands."""
        request = AgentRequest(
            "chore",
            templates.CHORE,
            Refined,
            metadata=self._route.metadata(self._route.worktree),
        )
        try:
            return await self._run.call(request)
        except AgentError as exception:
            logger.warning("chore discarded: %s", _reason(exception))
            return None

    async def _review(self, head: str) -> Reviewed | str:
        """review-pull-request; a failure is the reason finalize reports."""
        request = AgentRequest(
            "reviewer",
            templates.REVIEWER,
            Reviewed,
            args={"HEAD_SHA": head},
            metadata=self._route.metadata(self._route.checkout),
        )
        try:
            return await self._run.call(request)
        except AgentError as exception:
            return _reason(exception)

    async def _finalize(
        self, pull_request: PullRequest, head: str, review: Reviewed | str
    ) -> Outcome:
        """Marks the pull request ready only when nothing blocks it; never merges."""
        blockers = review_blockers(review, head)
        if not blockers:
            await self._tracker.mark_ready(pull_request)
            await self._comment(f"{pull_request.url} is ready for review")
            return Outcome("ready", pull_request=pull_request.url)
        reason = "; ".join(blockers)
        await self._comment(f"{pull_request.url} stays a draft: {reason}")
        await self._tracker.add_label(self._route.spec, HITL)
        return Outcome("draft", reason, pull_request.url)

    async def _hand_to_human(self, reason: str) -> None:
        await self._comment(reason)
        await self._tracker.add_label(self._route.spec, HITL)

    async def _escalate(self, ticket: int, reason: str) -> None:
        # The labels are written last, so both labels mean the escalation finished.
        message = f"{SOURCE}: {reason}"
        await self._tracker.comment(ticket, message)
        await self._tracker.comment(self._route.spec, message)
        await self._tracker.add_label(ticket, HITL)
        await self._tracker.add_label(self._route.spec, HITL)

    async def _comment(self, text: str) -> None:
        await self._tracker.comment(self._route.spec, f"{SOURCE}: {text}")


def _reason(exception: AgentError) -> str:
    return exception.reason if isinstance(exception, AgentDeclined) else str(exception)


def _numbers(numbers: list[int]) -> str:
    return ", ".join(f"#{number}" for number in numbers)


def _url(pull_request: PullRequest | None) -> str | None:
    return None if pull_request is None else pull_request.url
