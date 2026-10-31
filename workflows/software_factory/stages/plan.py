"""Plan Stages: a read-only planner proposes the Tickets that deliver a Spec, then the plan becomes Tickets on the tracker."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop import AgentWorkspace, Declined, Fail, Hold, Pass, ReadOnly, Stage, Verdict
from loop.shared.github.issues.client import Issues
from loop.shared.github.issues.graph import SpecGraphSnapshot
from loop.shared.github.issues.ticketing import (
    SpecTicketingService,
    TicketDraft,
    TicketingError,
)

from workflows.software_factory.agents.roles.planner import (
    PLANNER,
    PlannerReply,
    PlannerRequest,
)
from workflows.software_factory.events.specs import SpecHeld, SpecRouted, SpecWork
from workflows.software_factory.events.tickets import (
    PlannedTicket,
    PlanProposed,
    TicketsCreated,
)

MAX_PLANNED_TICKETS = 15

_PLAN_MARKER = re.compile(r"<!-- loop:plan (\{.*?\}) -->", re.DOTALL)


@dataclass(frozen=True)
class PlanInput:
    work: SpecWork
    routed: SpecRouted
    tracker: Issues
    checkout: AgentWorkspace


@dataclass(frozen=True)
class CreateInput:
    work: SpecWork
    tracker: Issues
    proposed: PlanProposed | None


@dataclass(frozen=True)
class _Refused:
    """The tracker stopped part-way through creating the Tickets."""

    reason: str


def plan_spec(
    *, max_tickets: int = MAX_PLANNED_TICKETS
) -> Stage[PlanInput, PlannerReply, PlanProposed]:
    """Read-only planner on the checkout, skipped unless the target plans and the Spec needs a plan; a decline holds, an invalid plan fails."""
    return (
        Stage("plan-spec", PlanInput, produces=PlanProposed, may_emit=(SpecHeld,))
        .agent(PLANNER, request=_request)
        .gate(lambda _, reply, __: _planned(reply, max_tickets))
        .contract(ReadOnly())
        .when(_needs_plan)
    )


def create_tickets(
    *, source: str = "implement_spec"
) -> Stage[CreateInput, Any, TicketsCreated]:
    """Persists the plan as a marker Comment on the Spec, then creates the Tickets and their blocked-by edges; resumes a persisted plan without duplicating."""
    return (
        Stage(
            "create-tickets", CreateInput, produces=TicketsCreated, may_emit=(SpecHeld,)
        )
        .effect(lambda i: _created(i) or _create(i, source), reconcile=_created)
        .gate(lambda _, reply, __: _accepted(reply))
        .when(_has_plan)
    )


def plan_violation(
    tickets: Sequence[PlannedTicket], max_tickets: int = MAX_PLANNED_TICKETS
) -> str | None:
    """Why a plan cannot become Tickets, or None when it can."""
    if not tickets:
        return "the planner returned no Tickets"
    if len(tickets) > max_tickets:
        return (
            f"the planner returned {len(tickets)} Tickets; the limit is {max_tickets}"
        )
    for index, ticket in enumerate(tickets):
        if not ticket.title.strip():
            return f"planned Ticket {index} has no title"
        if any(not 0 <= blocker < index for blocker in ticket.blocked_by):
            return f"planned Ticket {index} is blocked by a Ticket that does not come before it: {list(ticket.blocked_by)}"
    return None


def _needs_plan(i: PlanInput) -> bool:
    """A Spec with no sub-issues and no plan an earlier run kept; closed or held sub-issues prevent replanning."""
    if not i.routed.target.plan:
        return False
    spec = i.tracker.spec(i.work.number)
    return spec.needs_plan and _persisted_plan(spec) is None


def _request(i: PlanInput, path: Path) -> PlannerRequest:
    spec = i.tracker.spec(i.work.number)
    payload = {
        "number": spec.number,
        "title": spec.title,
        "url": spec.url,
        "body": spec.body,
        "comments": [
            {
                "author": comment.author,
                "body": comment.body,
                "created_at": comment.created_at,
            }
            for comment in spec.comments
        ],
    }
    return PlannerRequest(path, payload, i.routed.base_branch, i.routed.feature_branch)


def _planned(reply: PlannerReply, max_tickets: int) -> Verdict[PlanProposed]:
    if isinstance(reply, Declined):
        return Hold(reply.reason, SpecHeld(reply.reason))
    tickets = tuple(
        PlannedTicket(ticket.title, ticket.body, ticket.blocked_by)
        for ticket in reply.tickets
    )
    violation = plan_violation(tickets, max_tickets)
    if violation is not None:
        return Fail(violation, SpecHeld(violation))
    return Pass(PlanProposed(tickets))


def _has_plan(i: CreateInput) -> bool:
    """A plan was proposed in this run, or an earlier run left one part-way through its Tickets."""
    return (
        i.proposed is not None
        or _unfinished_plan(i.tracker.spec(i.work.number)) is not None
    )


def _accepted(reply: TicketsCreated | _Refused) -> Verdict[TicketsCreated]:
    if isinstance(reply, _Refused):
        return Fail(reply.reason, SpecHeld(reply.reason))
    return Pass(reply)


def _drafts(planned: Sequence[PlannedTicket]) -> tuple[TicketDraft, ...]:
    return tuple(
        TicketDraft(
            f"{index}-{_slug(ticket.title)}",
            ticket.title,
            ticket.body,
            ticket.blocked_by,
        )
        for index, ticket in enumerate(planned)
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


def _plan_of(i: CreateInput, spec: SpecGraphSnapshot) -> tuple[TicketDraft, ...]:
    """The proposed plan, else the persisted plan an earlier run left unfinished."""
    if i.proposed is not None:
        return _drafts(i.proposed.tickets)
    return _unfinished_plan(spec) or ()


def _created(i: CreateInput) -> TicketsCreated | None:
    """The creation a crashed attempt completed: the plan is persisted unchanged and every planned Ticket is linked."""
    spec = i.tracker.spec(i.work.number)
    drafts = _plan_of(i, spec)
    if (
        i.proposed is None
        or _persisted_plan(spec) != drafts
        or not spec.complete
        or spec.sub_issue_total < len(drafts)
    ):
        return None
    return TicketsCreated(tuple(ticket.number for ticket in spec.tickets))


def _create(i: CreateInput, source: str) -> TicketsCreated | _Refused:
    spec = i.tracker.spec(i.work.number)
    drafts = _plan_of(i, spec)
    # The tracker skips a Comment it already holds, so a resumed run does not post the plan twice.
    i.tracker.comment(spec.number, "plan", _plan_comment(source, drafts))
    try:
        created = SpecTicketingService(i.tracker).create(spec, drafts)
    except TicketingError as exception:
        return _Refused(str(exception))
    return TicketsCreated(tuple(ticket.number for ticket in created.tickets))


def _plan_comment(source: str, drafts: Sequence[TicketDraft]) -> str:
    """The Comment that keeps a validated plan on the Spec, so a retry creates its Tickets instead of planning again."""
    payload = json.dumps(
        {
            "tickets": [
                {
                    "key": d.key,
                    "title": d.title,
                    "body": d.body,
                    "blocked_by": list(d.blocked_by),
                }
                for d in drafts
            ]
        }
    )
    # Angle brackets are escaped so a Ticket body cannot end the hidden marker early.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    summary = "\n".join(
        f"{index + 1}. {draft.title}" for index, draft in enumerate(drafts)
    )
    return f"{source}: plan\n\n{summary}\n\n<!-- loop:plan {payload} -->"


def _persisted_plan(spec: SpecGraphSnapshot) -> tuple[TicketDraft, ...] | None:
    """The newest plan an earlier run kept on the Spec, or None when there is none or it cannot be read."""
    for comment in reversed(spec.comments):
        match = _PLAN_MARKER.search(comment.body)
        if match is None:
            continue
        try:
            return tuple(
                TicketDraft(
                    ticket["key"],
                    ticket["title"],
                    ticket["body"],
                    tuple(ticket["blocked_by"]),
                )
                for ticket in json.loads(match[1])["tickets"]
            )
        except ValueError, KeyError, TypeError:
            return None
    return None


def _unfinished_plan(spec: SpecGraphSnapshot) -> tuple[TicketDraft, ...] | None:
    """The persisted plan when fewer Tickets than it plans are linked to the Spec: an earlier run stopped part-way."""
    drafts = _persisted_plan(spec)
    return (
        drafts
        if drafts is not None and spec.complete and spec.sub_issue_total < len(drafts)
        else None
    )
