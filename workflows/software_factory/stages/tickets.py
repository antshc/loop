"""Ticket Stages: select READY Tickets, implement one with the developer, close it, escalate it and require the delivery."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop import (
    AgentCommits,
    AgentWorkspace,
    All,
    Declined,
    Evidence,
    Fail,
    History,
    Hold,
    Item,
    Json,
    Pass,
    Selector,
    Stage,
    Verdict,
    rework,
)
from loop.shared.git import GitClient
from loop.shared.github.issues.client import Issues
from loop.shared.github.issues.graph import SpecGraphSnapshot
from loop.shared.github.issues.issue import IssueState
from loop.shared.github.issues.spec import Ticket

from workflows.software_factory.agents.roles.developer import (
    DEVELOPER,
    DeveloperReply,
    DeveloperRequest,
)
from workflows.software_factory.events.specs import SpecRouted, SpecWork
from workflows.software_factory.events.tickets import (
    Delivery,
    TicketClosed,
    TicketEscalated,
    TicketImplemented,
    TicketRejected,
)

DEFAULT_REWORK = 2


@dataclass(frozen=True)
class SpecInput:
    work: SpecWork
    tracker: Issues


@dataclass(frozen=True)
class TicketInput:
    ticket: Ticket
    routed: SpecRouted
    worktree: AgentWorkspace
    git: GitClient


@dataclass(frozen=True)
class CloseInput:
    ticket: Ticket
    implemented: TicketImplemented
    tracker: Issues


@dataclass(frozen=True)
class EscalateInput:
    work: SpecWork
    ticket: Ticket
    tracker: Issues
    rejected: History[TicketRejected]


@dataclass(frozen=True)
class DeliveredInput:
    work: SpecWork
    routed: SpecRouted
    worktree: AgentWorkspace
    tracker: Issues
    git: GitClient
    escalated: All[TicketEscalated]


def next_ready_ticket() -> Selector[SpecInput, Ticket]:
    """Re-reads the Spec from the tracker, which owns Ticket state; the actionable READY Tickets in order, each with its body digest as revision."""
    return Selector("next-ready-ticket", SpecInput, Ticket, _ready)


def implement_ticket() -> Stage[TicketInput, DeveloperReply, TicketImplemented]:
    """Fresh developer session per attempt; a decline fails; the agent commits exactly one validated commit; rework twice, then stop failed."""
    return (
        Stage(
            "implement-ticket",
            TicketInput,
            produces=TicketImplemented,
            may_emit=(TicketRejected,),
        )
        .agent(DEVELOPER, request=_request)
        .gate(_implemented)
        .contract(
            AgentCommits(
                subject=lambda i: i.routed.subject_prefix(i.ticket.number),
                reported=lambda e: e.commit,
            )
        )
        .on_fail(rework(max_attempts=DEFAULT_REWORK))
        .attributes(lambda i: {"ticket": i.ticket.number})
    )


def close_ticket() -> Stage[CloseInput, TicketClosed, TicketClosed]:
    """Closes the Ticket with the delivery comment; a Ticket that is already closed is adopted without a second comment."""
    return (
        Stage("close-ticket", CloseInput, produces=TicketClosed)
        .effect(
            lambda i: _closed(i) or _close(i),
            reconcile=_closed,
        )
        .attributes(lambda i: {"ticket": i.ticket.number})
    )


def escalate_ticket(
    *, label: str, source: str = "implement_spec"
) -> Stage[EscalateInput, TicketEscalated, TicketEscalated]:
    """Labels the Ticket and its Spec `label` and comments why; the labels are written last, so both labels mean it was done."""
    return (
        Stage("escalate-ticket", EscalateInput, produces=TicketEscalated)
        .effect(
            lambda i: _escalated(label)(i) or _escalate(label, source)(i),
            reconcile=_escalated(label),
        )
        .attributes(lambda i: {"ticket": i.ticket.number})
    )


def require_delivered() -> Stage[DeliveredInput, SpecGraphSnapshot, Delivery]:
    """Fails after any escalation in this run, holds while Tickets remain held, otherwise passes with the delivery head and commits."""
    return (
        Stage("require-delivered", DeliveredInput, produces=Delivery)
        .action(lambda i: i.tracker.spec(i.work.number))
        .gate(_delivered)
    )


def _ready(i: SpecInput) -> Sequence[Item[Ticket]]:
    spec = i.tracker.spec(i.work.number)
    return [
        Item(
            ticket,
            f"#{ticket.number}",
            hashlib.sha256(ticket.body.encode()).hexdigest(),
        )
        for ticket in spec.deliverable_tickets
        if ticket.actionable
    ]


def _request(i: TicketInput, path: Path) -> DeveloperRequest:
    prefix = i.routed.subject_prefix(i.ticket.number)
    initiative_prefix = f"ccode({i.routed.initiative}|"
    commits = i.git.initiative_commits(path, i.routed.base_branch, initiative_prefix)
    return DeveloperRequest(
        f"{i.routed.initiative}|{i.ticket.number}",
        _ticket_payload(i.ticket),
        tuple(f"{commit.sha[:7]} {commit.subject}" for commit in commits),
        prefix,
        path,
        i.routed.base_branch,
        i.routed.feature_branch,
    )


def _ticket_payload(ticket: Ticket) -> dict[str, Json]:
    """The JSON the developer prompt shows for a Ticket."""
    return {
        "number": ticket.number,
        "title": ticket.title,
        "state": str(ticket.state),
        "labels": list(ticket.labels),
        "url": ticket.url,
        "body": ticket.body,
        "comments": [
            {
                "author": comment.author,
                "body": comment.body,
                "created_at": comment.created_at,
            }
            for comment in ticket.comments
        ],
    }


def _implemented(
    i: TicketInput, reply: DeveloperReply, _: Evidence
) -> Verdict[TicketImplemented]:
    if isinstance(reply, Declined):
        return Fail(reply.reason, TicketRejected(i.ticket.number, reply.reason))
    return Pass(
        TicketImplemented(
            i.ticket.number, reply.commit, reply.summary, reply.verification
        )
    )


def _closed(i: CloseInput) -> TicketClosed | None:
    """The closure a crashed attempt already made."""
    if i.tracker.issue(i.ticket.number).state is IssueState.CLOSED:
        return TicketClosed(i.ticket.number, i.implemented.commit, i.ticket.title)
    return None


def _close(i: CloseInput) -> TicketClosed:
    implemented = i.implemented
    comment = f"Delivered in {implemented.commit}.\n\n{implemented.summary}\n\n{implemented.verification}"
    i.tracker.close(i.ticket.number, comment)
    return TicketClosed(i.ticket.number, implemented.commit, i.ticket.title)


def _escalation_reason(i: EscalateInput) -> str:
    rejected = (
        event.reason
        for event in reversed(i.rejected)
        if event.ticket == i.ticket.number
    )
    return next(rejected, "the Ticket was rejected after repeated attempts")


def _escalated(label: str) -> Any:
    def reconcile(i: EscalateInput) -> TicketEscalated | None:
        labelled = (
            label in i.tracker.issue(i.ticket.number).labels
            and label in i.tracker.issue(i.work.number).labels
        )
        return (
            TicketEscalated(i.ticket.number, label, _escalation_reason(i))
            if labelled
            else None
        )

    return reconcile


def _escalate(label: str, source: str) -> Any:
    def run(i: EscalateInput) -> TicketEscalated:
        reason = _escalation_reason(i)
        message = f"{source}: {reason}"
        i.tracker.comment(i.ticket.number, "escalation", message)
        i.tracker.comment(i.work.number, "hand-off", message)
        i.tracker.add_label(i.ticket.number, label)
        i.tracker.add_label(i.work.number, label)
        return TicketEscalated(i.ticket.number, label, reason)

    return run


def _delivered(
    i: DeliveredInput, spec: SpecGraphSnapshot, _: Evidence
) -> Verdict[Delivery]:
    if i.escalated:
        numbers = ", ".join(f"#{event.ticket}" for event in i.escalated)
        return Fail(f"Tickets were escalated to a person in this run: {numbers}")
    if spec.held_tickets:
        numbers = ", ".join(f"#{number}" for number in spec.held_tickets)
        return Hold(f"Tickets remain held: {numbers}")
    undelivered = [
        ticket.number for ticket in spec.deliverable_tickets if ticket.actionable
    ]
    if undelivered:
        numbers = ", ".join(f"#{number}" for number in undelivered)
        return Fail(
            f"Tickets remain undelivered although the delivery loop ended: {numbers}"
        )
    path = i.worktree.path
    commits = tuple(
        f"{commit.sha[:7]} {commit.subject}"
        for commit in i.git.branch_commits(path, i.routed.base_branch)
    )
    return Pass(Delivery(i.git.head(path).sha, commits))
