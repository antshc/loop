"""The plan stage: a read-only planner proposes Tickets for a Spec without sub-issues; the harness validates the plan and creates them."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from loop import Kind, Phase, Phases

from workflows.shared.agents.launcher import AgentLauncher
from workflows.shared.git import GitClient
from workflows.shared.github.issues.errors import IssueError
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.spec import Ticket
from workflows.shared.process import CommandError

from ..model.branches import slugify
from ..model.definition import Owner
from ..model.gates import readonly_violation
from ..model.handoff import hand_to_human
from ..model.records import (
    PlanContext, PlannedTicket, PlanResult, PlanWorkflow, TicketWorkflowError, loop_session_name,
)
from ..model.repositories import Tracker


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


def _with_blockers(planned: PlannedTicket, earlier: list[int]) -> str:
    blockers = [f"#{earlier[index]}" for index in planned.blocked_by if 0 <= index < len(earlier)]
    return f"{planned.body.rstrip()}\n\nBlocked by {', '.join(blockers)}" if blockers else planned.body


def _ticket_key(index: int, planned: PlannedTicket) -> str:
    return f"{index}-{slugify(planned.title)}"


class Planner:
    """Runs the planner read-only on a checkout and turns a valid plan into Tickets on the tracker."""

    def __init__(self, new_agent: AgentLauncher, git: GitClient, tracker: Tracker, phases: Phases) -> None:
        self._new_agent = new_agent
        self._git = git
        self._tracker = tracker
        self._phases = phases

    def plan(self, spec: SpecGraphSnapshot, checkout: Path, run_plan: PlanWorkflow) -> SpecGraphSnapshot | None:
        """Plans a Spec with no sub-issues; returns it with its new Tickets, else hands the Spec to a human with the reason and returns None."""
        base_branch = spec.base_branch
        if base_branch is None:
            raise TicketWorkflowError(f"cannot resolve base branch for Spec {spec.url}")
        with self._phases.phase("plan", Kind.AGENT, Owner.PLANNER, spec=spec.number) as phase:
            head, status = self._git.head(checkout), self._git.status(checkout)
            try:
                with self._new_agent().open() as workspace:
                    context = PlanContext(spec, base_branch, loop_session_name(spec.number, spec.title), source_root=checkout)
                    result = run_plan(context, workspace)
            except (subprocess.CalledProcessError, TicketWorkflowError) as exception:
                return self._reject(spec, phase, f"the planner failed: {exception}")
            violation = readonly_violation(self._git, checkout, head.sha, status)
            if not phase.gate("planner left the checkout unchanged", violation is None, violation or ""):
                return self._reject(spec, phase, violation or "")
            violation = plan_violation(result)
            if not phase.gate("plan is valid", violation is None, violation or ""):
                return self._reject(spec, phase, violation or "")
            phase.set(planned=len(result.tickets))
        with self._phases.phase("create-tickets", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            try:
                created = self._create_tickets(spec, result.tickets)
            except TicketWorkflowError as exception:
                return self._reject(spec, phase, str(exception))
            phase.set(tickets=",".join(str(ticket.number) for ticket in created))
        planned = spec.with_created(created)
        self._tracker.comment(spec.number, "planned", "dev: planned " + ", ".join(f"#{ticket.number}" for ticket in created))
        return planned

    def _create_tickets(self, spec: SpecGraphSnapshot, planned: tuple[PlannedTicket, ...]) -> tuple[Ticket, ...]:
        """Finds or creates each planned Ticket, then links it to `spec`; a failure names the Tickets already made and keeps them."""
        tracker = self._tracker
        created: list[Ticket] = []
        try:
            for index, planned_ticket in enumerate(planned):
                key = _ticket_key(index, planned_ticket)
                body = _with_blockers(planned_ticket, [ticket.number for ticket in created])
                ticket = tracker.find_ticket(spec, key) or tracker.create_ticket(spec, key, planned_ticket.title, body)
                tracker.link_ticket(spec, ticket)
                created.append(ticket)
        except (CommandError, IssueError, TicketWorkflowError) as exception:
            made = ", ".join(f"#{ticket.number}" for ticket in created) or "none"
            raise TicketWorkflowError(f"creating Tickets failed after creating {made}: {exception}") from exception
        return tuple(created)

    def _reject(self, spec: SpecGraphSnapshot, phase: Phase, reason: str) -> None:
        logger.warning("spec #%s not planned: %s", spec.number, reason)
        phase.fail("Planning failed; human input required")
        hand_to_human(self._tracker, spec, f"dev: cannot plan this Spec: {reason}")
