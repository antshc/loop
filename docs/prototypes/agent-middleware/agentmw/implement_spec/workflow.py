"""implement_spec as one declarative definition: plan, deliver each ready Ticket, publish, chore, review, finalize."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import PromptError
from ..flow import Flow, Workflow, handoff, next_, rework, stop
from ..host import AgentHost
from ..integrations import WorktreeGit
from .stages import (
    announce_delivered,
    checkout,
    close_ticket,
    comment_failure,
    create_tickets,
    escalate_ticket,
    feature_worktree,
    finalize,
    hand_to_human,
    implement_ticket,
    next_ready_ticket,
    plan_spec,
    publish_draft_pr,
    refine_chore,
    require_delivered,
    require_work,
    review_pull_request,
)
from .state import Outcome, SpecRoute, SpecRun
from .tracker import PullRequest, Tracker

NAME = "implement_spec"

DEFAULT_REWORK = 2

# What the Workflow turns into a failure comment when it escapes a Stage.
FAILURE_ERRORS: tuple[type[Exception], ...] = (PromptError, LookupError)


@dataclass(frozen=True)
class ImplementSpecSettings:
    """Per-run settings the definition reads."""

    max_failures: int = DEFAULT_REWORK


def implement_spec(settings: ImplementSpecSettings) -> Workflow[SpecRun]:
    """One Spec, end to end: plan, deliver each ready Ticket, publish, chore, review, finalize."""
    to_human = handoff(hand_to_human)
    escalate = handoff(escalate_ticket, status="escalated")
    ticket = (
        Flow("ticket")
        .stage(
            implement_ticket().on_fail(
                rework(max_attempts=settings.max_failures, then=escalate)
            )
        )
        .stage(close_ticket())
        .stage(publish_draft_pr().named("publish-ticket"))
    )
    return (
        Workflow(NAME)
        .on_error(FAILURE_ERRORS, handoff(comment_failure, status="failed"))
        .within(
            checkout(),
            Flow("plan")
            .stage(plan_spec().on_hold(to_human).on_fail(to_human))
            .stage(create_tickets().on_fail(to_human)),
        )
        .stage(publish_draft_pr().named("publish-earlier"))
        .stage(require_work().on_hold(stop.idle()))
        .within(
            feature_worktree(),
            Flow("deliver")
            .each(next_ready_ticket(), ticket, on_fail=next_())
            .stage(publish_draft_pr().named("publish-delivery"))
            .stage(require_delivered().on_hold(stop.idle()))
            .stage(announce_delivered().on_hold(stop.idle()))
            .stage(refine_chore().on_fail(next_()))
            .stage(publish_draft_pr().named("publish-chore"))
            .stage(require_delivered().named("require-refined").on_hold(stop.idle())),
        )
        .within(
            checkout(),
            Flow("review").stage(review_pull_request().on_fail(next_())),
        )
        .stage(finalize())
    )


class ImplementSpec:
    """Runs the definition for one Spec in one run scope of `host`."""

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
        self._definition = implement_spec(ImplementSpecSettings(max_failures))

    async def run(self, route: SpecRoute) -> Outcome:
        """The Outcome of the Spec's run; agent failures end in a hand-off, never an exception."""
        async with self._host.run_scope() as agents:
            ctx = SpecRun(route, agents, self._tracker, self._git)
            stopped = await self._definition.run(ctx)
        if stopped is None:
            return ctx.results["finalize"]
        pull_request: PullRequest | None = ctx.results.get("publish-delivery")
        url = None if pull_request is None else pull_request.url
        return Outcome(stopped.status, stopped.reason, url)
