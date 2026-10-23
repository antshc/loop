"""The plan stage: a read-only planner proposes Tickets for a Spec without sub-issues; the harness validates the plan and creates them."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from loop import AgentBuilder, Kind, Phase, Phases

from .checkout import GitService
from .gates import readonly_violation
from .definition import Owner
from .platforms import TicketsTracker
from .records import PlanContext, PlanResult, PlanWorkflow, Spec, TicketWorkflowError, loop_session_name


logger = logging.getLogger("workflow.plan")


MAX_PLANNED_TICKETS = 15


def plan_violation(result: PlanResult) -> str | None:
    """Why a planner's answer cannot become Tickets, or None when it can."""
    if result.status == "failed":
        return result.reason or "the planner could not plan the Spec"
    if result.status != "completed":
        return f"unknown plan status: {result.status!r}"
    if not result.tickets:
        return "the planner returned no Tickets"
    if len(result.tickets) > MAX_PLANNED_TICKETS:
        return f"the planner returned {len(result.tickets)} Tickets; the limit is {MAX_PLANNED_TICKETS}"
    for index, ticket in enumerate(result.tickets):
        if not ticket.title.strip():
            return f"planned Ticket {index} has no title"
        if any(not 0 <= blocker < index for blocker in ticket.blocked_by):
            return f"planned Ticket {index} is blocked by a Ticket that does not come before it: {list(ticket.blocked_by)}"
    return None


def plan_spec(
    spec: Spec,
    checkout: Path,
    run_plan: PlanWorkflow,
    *,
    new_agent: Callable[[], AgentBuilder],
    git: GitService,
    tracker: TicketsTracker,
    phases: Phases,
) -> bool:
    """Plans a Spec with no sub-issues; returns whether it now has Tickets, else hands the Spec to a human with the reason."""
    base_branch = spec.base_branch
    if base_branch is None:
        raise TicketWorkflowError(f"cannot resolve base branch for Spec {spec.url}")
    with phases.phase("plan", Kind.AGENT, Owner.PLANNER, spec=spec.number) as phase:
        head, status = git.head(checkout), git.status(checkout)
        try:
            with new_agent().open() as workspace:
                result = run_plan(PlanContext(spec, base_branch, loop_session_name(spec.number, spec.title), source_root=checkout), workspace)
        except (subprocess.CalledProcessError, TicketWorkflowError) as exception:
            return _reject(spec, tracker, phase, f"the planner failed: {exception}")
        violation = readonly_violation(git, checkout, head.sha, status)
        if not phase.gate("planner left the checkout unchanged", violation is None, violation or ""):
            return _reject(spec, tracker, phase, violation or "")
        violation = plan_violation(result)
        if not phase.gate("plan is valid", violation is None, violation or ""):
            return _reject(spec, tracker, phase, violation or "")
        phase.set(planned=len(result.tickets))
    with phases.phase("create-tickets", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
        try:
            created = tracker.create_tickets(spec, result.tickets)
        except TicketWorkflowError as exception:
            return _reject(spec, tracker, phase, str(exception))
        phase.set(tickets=",".join(str(ticket.number) for ticket in created))
    spec.comment("dev: planned " + ", ".join(f"#{ticket.number}" for ticket in created))
    tracker.update_spec(spec)
    return True


def _reject(spec: Spec, tracker: TicketsTracker, phase: Phase, reason: str) -> bool:
    logger.warning("spec #%s not planned: %s", spec.number, reason)
    phase.fail("Planning failed; human input required")
    spec.hand_to_human(f"dev: cannot plan this Spec: {reason}")
    tracker.update_spec(spec)
    return False
