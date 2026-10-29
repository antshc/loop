"""The plan stage: a read-only planner proposes Tickets for a Spec without sub-issues; the harness validates the plan and creates them."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from loop import Kind, Phase, Phases

from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.issues.errors import IssueError
from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.issues.ticketing import SpecTicketingService, TicketDraft, TicketingError

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


_PLAN_MARKER = re.compile(r"<!-- loop:plan (\{.*?\}) -->", re.DOTALL)


def _plan_comment(drafts: tuple[TicketDraft, ...]) -> str:
    """The Comment that keeps a validated plan on the Spec, so a retry creates its Tickets instead of planning again."""
    payload = json.dumps(
        {"tickets": [{"key": d.key, "title": d.title, "body": d.body, "blocked_by": list(d.blocked_by)} for d in drafts]}
    )
    # Angle brackets are escaped so a Ticket body cannot end the hidden marker early.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    summary = "\n".join(f"{index + 1}. {draft.title}" for index, draft in enumerate(drafts))
    return f"implement_spec: plan\n\n{summary}\n\n<!-- loop:plan {payload} -->"


def persisted_plan(spec: SpecGraphSnapshot) -> tuple[TicketDraft, ...] | None:
    """The plan kept on the Spec by an earlier run, or None when there is none or it cannot be read."""
    for comment in reversed(spec.comments):
        match = _PLAN_MARKER.search(comment.body)
        if match is None:
            continue
        try:
            return tuple(
                TicketDraft(ticket["key"], ticket["title"], ticket["body"], tuple(ticket["blocked_by"]))
                for ticket in json.loads(match[1])["tickets"]
            )
        except (ValueError, KeyError, TypeError):
            return None
    return None


def unfinished_plan(spec: SpecGraphSnapshot) -> tuple[TicketDraft, ...] | None:
    """The persisted plan when fewer Tickets than it plans are linked to the Spec: an earlier run stopped part-way through creating them."""
    drafts = persisted_plan(spec)
    return drafts if drafts is not None and spec.complete and spec.sub_issue_total < len(drafts) else None


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
        """Plans a Spec with no sub-issues, or finishes the plan an earlier run left part-way; returns it with its new Tickets, else hands the Spec to a human with the reason and returns None."""
        base_branch = spec.base_branch
        if base_branch is None:
            raise TicketWorkflowError(f"cannot resolve base branch for Spec {spec.url}")
        drafts = unfinished_plan(spec)
        if drafts is not None:
            with self._phases.phase("plan", Kind.AGENT, Owner.PLANNER, spec=spec.number) as phase:
                phase.skip("Resuming the plan an earlier run kept on the Spec")
        else:
            drafts = self._propose(spec, checkout, run_plan, base_branch)
            if drafts is None:
                return None
        with self._phases.phase("create-tickets", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            try:
                self._tracker.comment(spec.number, "plan", _plan_comment(drafts))
                created = self._ticketing.create(spec, drafts)
            except (TicketingError, IssueError, subprocess.CalledProcessError) as exception:
                return self._reject(spec, phase, str(exception))
            phase.set(tickets=",".join(str(ticket.number) for ticket in created.tickets))
        known = {ticket.number for ticket in spec.tickets}
        edges = {(edge.ticket, edge.blocker) for edge in spec.blocked_by}
        planned = spec.with_created(
            [ticket for ticket in created.tickets if ticket.number not in known],
            [edge for edge in created.blocked_by if (edge.ticket, edge.blocker) not in edges],
        )
        self._tracker.comment(spec.number, "planned", "implement_spec: planned " + ", ".join(f"#{ticket.number}" for ticket in created.tickets))
        return planned

    def _propose(
        self, spec: SpecGraphSnapshot, checkout: Path, run_plan: PlanWorkflow, base_branch: str
    ) -> tuple[TicketDraft, ...] | None:
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
            return _drafts(result.tickets)

    def _reject(self, spec: SpecGraphSnapshot, phase: Phase, reason: str) -> None:
        logger.warning("spec #%s not planned: %s", spec.number, reason)
        phase.fail("Planning failed; human input required")
        hand_to_human(self._tracker, spec, f"implement_spec: cannot plan this Spec: {reason}")
