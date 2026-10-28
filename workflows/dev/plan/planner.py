"""The plan stage: a read-only planner proposes Tickets for a Spec without sub-issues; the harness validates the plan and creates them."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from loop import Kind, Phase, Phases

from workflows.shared.agents.launcher import AgentLauncher
from workflows.shared.git import GitClient
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.ticketing import SpecTicketingService, TicketDraft, TicketingError

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


def _drafts(planned: tuple[PlannedTicket, ...]) -> tuple[TicketDraft, ...]:
    return tuple(
        TicketDraft(f"{index}-{slugify(ticket.title)}", ticket.title, ticket.body, ticket.blocked_by)
        for index, ticket in enumerate(planned)
    )


class Planner:
    """Runs the planner read-only on a checkout and turns a valid plan into Tickets on the tracker."""

    def __init__(
        self, new_agent: AgentLauncher, git: GitClient, tracker: Tracker, ticketing: SpecTicketingService, phases: Phases,
    ) -> None:
        self._new_agent = new_agent
        self._git = git
        self._tracker = tracker
        self._ticketing = ticketing
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
                created = self._ticketing.create(spec, _drafts(result.tickets))
            except TicketingError as exception:
                return self._reject(spec, phase, str(exception))
            phase.set(tickets=",".join(str(ticket.number) for ticket in created.tickets))
        planned = spec.with_created(created.tickets, created.blocked_by)
        self._tracker.comment(spec.number, "planned", "dev: planned " + ", ".join(f"#{ticket.number}" for ticket in created.tickets))
        return planned

    def _reject(self, spec: SpecGraphSnapshot, phase: Phase, reason: str) -> None:
        logger.warning("spec #%s not planned: %s", spec.number, reason)
        phase.fail("Planning failed; human input required")
        hand_to_human(self._tracker, spec, f"dev: cannot plan this Spec: {reason}")
