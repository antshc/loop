"""implement_spec as one declarative definition over the Stages library: route, plan, deliver each ready Ticket, publish, chore, review, finalize."""

from __future__ import annotations

from subprocess import CalledProcessError

from loop import LoopHookError

from software_factory.events.pull_requests import DraftPullRequestPublished
from software_factory.runtime import Flow, Workflow, handoff, next_, rework, stop
from software_factory.shared.github.issues.errors import IssueError
from software_factory.stages import chore, finalize, plan, publish, review, specs, tickets, workspaces

from .policy import HITL, SOURCE, SpecWork, chore_subject, held_by_spec, held_by_ticket
from .settings import ImplementSpecSettings, Targets

NAME = "implement_spec"

__all__ = ["ImplementSpecSettings", "NAME", "Targets", "implement_spec"]


def implement_spec(settings: ImplementSpecSettings, targets: Targets) -> Workflow[SpecWork]:
    """One Spec, end to end: route, plan, deliver each ready Ticket, publish, chore, review, finalize."""
    to_human = handoff(specs.hand_to_human(label=HITL, source=SOURCE).records_run(held_by_spec))
    block = handoff(specs.block_spec(label=HITL, source=SOURCE).records_run(held_by_spec))
    escalate = handoff(tickets.escalate_ticket(label=HITL, source=SOURCE).records_run(held_by_ticket))
    ticket = (
        Flow("ticket")
        .stage(tickets.implement_ticket().on_fail(rework(max_attempts=settings.max_failures, then=escalate)))
        .stage(tickets.close_ticket())
        .stage(publish.publish_draft_pr().named("publish-ticket"))
    )
    return (
        Workflow(NAME).context(SpecWork)
        .on_error((LoopHookError, CalledProcessError, IssueError), handoff(specs.comment_failure(source=SOURCE)))
        .stage(specs.route_spec(targets.repositories).on_hold(block))
        .stage(specs.prepare_checkout().on_hold(to_human))
        .within(workspaces.checkout(), Flow("plan")
            .stage(plan.plan_spec().on_hold(to_human).on_fail(to_human))
            .stage(plan.create_tickets(source=SOURCE).on_fail(to_human)))
        .stage(publish.publish_draft_pr().named("publish-earlier"))
        .stage(specs.require_work().on_hold(stop.idle()))
        .within(workspaces.feature_worktree(hooks=settings.loop_hooks), Flow("deliver")
            .each(tickets.next_ready_ticket(), ticket, on_fail=next_())
            .stage(publish.publish_draft_pr().named("publish-delivery"))
            .stage(tickets.require_delivered().on_hold(stop.idle()))
            .stage(specs.announce_delivered(source=SOURCE).reads(DraftPullRequestPublished, from_="publish-delivery"))
            .stage(chore.refine(subject=chore_subject).on_fail(next_())))
        .within(workspaces.checkout(), Flow("review")
            .stage(review.review_pull_request().on_fail(next_())))
        .stage(finalize.finalize(label=HITL, source=SOURCE).reads(DraftPullRequestPublished, from_="publish-delivery"))
    )
