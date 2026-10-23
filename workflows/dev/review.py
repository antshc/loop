"""The review and finalize stages: a read-only reviewer runs `code-review` on the pushed PR, then the harness decides readiness."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from loop import AgentBuilder, Kind, Phases

from .chore import ChoreReport
from .checkout import GitService
from .commands import CommandError
from .gates import readonly_violation
from .definition import Owner
from .platforms import PullRequestClient, TicketsTracker
from .records import (
    HITL_LABEL, Repository, ReviewContext, ReviewResult, ReviewWorkflow, Spec, TicketWorkflowError, loop_session_name,
)
from .verify import SKIPPED, VerifyReport


@dataclass(frozen=True)
class Delivery:
    """What the worktree phase produced: the published pull request at its final head, and how each step ended."""

    pull_request_url: str
    head_sha: str
    delivered: bool
    commits: tuple[str, ...] = ()
    verification: VerifyReport = SKIPPED
    chore: ChoreReport | None = None


@dataclass(frozen=True)
class ReviewOutcome:
    result: ReviewResult | None = None
    error: str = ""


def review_pull_request(
    spec: Spec,
    delivery: Delivery,
    repository: Repository,
    run_review: ReviewWorkflow,
    *,
    new_agent: Callable[[], AgentBuilder],
    git: GitService,
    phases: Phases,
) -> ReviewOutcome:
    """Runs the reviewer from the checkout once the implementation worktree is gone; a read-only gate guards the checkout."""
    checkout = repository.checkout
    with phases.phase("review", Kind.AGENT, Owner.REVIEWER, spec=spec.number, head=delivery.head_sha) as phase:
        head, status = git.head(checkout), git.status(checkout)
        context = ReviewContext(
            delivery.pull_request_url, checkout, spec.initiative, delivery.head_sha, loop_session_name(spec.number, spec.title)
        )
        try:
            with new_agent().open() as workspace:
                result = run_review(context, workspace)
        except (subprocess.CalledProcessError, TicketWorkflowError) as exception:
            phase.fail("Reviewer could not complete")
            return ReviewOutcome(error=f"the reviewer failed: {exception}")
        violation = readonly_violation(git, checkout, head.sha, status)
        if not phase.gate("reviewer left the checkout unchanged", violation is None, violation or ""):
            return ReviewOutcome(error=violation or "")
        phase.gate("every axis completed", result.completed, "an axis did not complete")
        phase.gate("reviewed the current head", result.head_sha == delivery.head_sha, f"reviewed {result.head_sha!r}")
        phase.set(findings=len(result.findings), blocking_findings=len(result.blocking_findings), posted=len(result.posted))
    return ReviewOutcome(result)


def readiness_blockers(spec: Spec, delivery: Delivery, outcome: ReviewOutcome, current_head: str) -> list[str]:
    """Why the PR must stay a draft; empty when every gate passes."""
    blockers: list[str] = []
    if not delivery.delivered:
        blockers.append("not every Ticket was delivered")
    open_tickets = [ticket for ticket in spec.tickets if ticket.state == "open"]
    if open_tickets:
        blockers.append("open Tickets: " + ", ".join(f"#{ticket.number}" for ticket in open_tickets))
    if spec.held_tickets:
        blockers.append("Tickets held for a human: " + ", ".join(f"#{number}" for number in spec.held_tickets))
    if spec.awaiting_human or any(HITL_LABEL in ticket.labels for ticket in spec.tickets):
        blockers.append("a Ticket or the Spec is waiting for a human (`hitl`)")
    if delivery.verification.blocks_ready:
        blockers.append(f"app verification is {delivery.verification.verdict}")
    result = outcome.result
    if result is None:
        blockers.append(outcome.error or "the review did not run")
    else:
        if not result.completed:
            blockers.append("a review axis did not complete")
        if result.head_sha != delivery.head_sha:
            blockers.append(f"the review covered {result.head_sha or 'an unknown head'}, not {delivery.head_sha}")
        if result.blocking_findings:
            blockers.append(f"{len(result.blocking_findings)} unresolved bug/initiative finding(s)")
    if current_head != delivery.head_sha:
        blockers.append(f"the PR head moved to {current_head} after the review started")
    return blockers


def finalize(
    spec: Spec,
    delivery: Delivery,
    outcome: ReviewOutcome,
    repository: Repository,
    *,
    git: GitService,
    pulls: PullRequestClient,
    tracker: TicketsTracker,
    phases: Phases,
) -> bool:
    """Marks the PR ready only when every gate passes and comments the summary on the Spec; never merges or closes the Spec."""
    feature_branch = spec.feature_branch
    if feature_branch is None:
        raise TicketWorkflowError(f"cannot resolve feature branch for Spec {spec.url}")
    with phases.phase("finalize", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
        git.fetch(repository.checkout)
        current_head = git.ref_sha(repository.checkout, f"origin/{feature_branch}")
        blockers = readiness_blockers(spec, delivery, outcome, current_head)
        phase.gate("PR head is unchanged since the review", current_head == delivery.head_sha, current_head)
        ready = not blockers
        if ready:
            try:
                pulls.mark_ready(delivery.pull_request_url)
            except CommandError as exception:
                ready = False
                blockers.append(f"marking the PR ready failed: {exception}")
                phase.fail(blockers[-1])
        phase.set(ready=ready)
        if not ready:
            phase.fail("PR remains a draft: readiness checks did not pass")
        spec.comment(_summary(delivery, outcome, ready, blockers))
        tracker.update_spec(spec)
    return ready


def _summary(delivery: Delivery, outcome: ReviewOutcome, ready: bool, blockers: list[str]) -> str:
    lines = [
        f"dev: pull request {delivery.pull_request_url} is "
        + ("**ready for review**" if ready else "**left as a draft**"),
        "",
        "Commits:",
        *(f"- {commit}" for commit in delivery.commits),
        "",
        f"App verification: {delivery.verification.verdict}"
        + (f" (evidence `{delivery.verification.evidence_dir}`)" if delivery.verification.evidence_dir else ""),
    ]
    if delivery.chore is not None:
        lines.append(f"Chore: {delivery.chore.status}" + (f" ({delivery.chore.note})" if delivery.chore.note else ""))
        lines += [f"- out of scope: {item}" for item in delivery.chore.out_of_scope]
    result = outcome.result
    if result is None:
        lines.append(f"Review: not completed ({outcome.error or 'it did not run'})")
    else:
        lines.append("Review: " + ", ".join(f"{axis.name} {axis.outcome}" for axis in result.axes))
        lines += [f"- {finding.kind}: {finding.summary}" for finding in result.findings]
    if blockers:
        lines += ["", "Kept as a draft because:", *(f"- {blocker}" for blocker in blockers)]
    return "\n".join(lines)
