"""The review and finalize stages: a read-only reviewer runs `code-review` on the pushed PR, then the harness decides readiness."""

from __future__ import annotations

import subprocess

from loop import Kind, Phases

from workflows.shared.agents.launcher import AgentLauncher
from workflows.shared.git import GitClient
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.pull_requests import PullRequests

from ..model.branches import feature_branch as feature_branch_of
from ..model.definition import Owner
from ..model.gates import readonly_violation
from ..model.outcomes import Delivery, ReviewOutcome
from ..model.records import HITL_LABEL, Repository, ReviewContext, ReviewWorkflow, TicketWorkflowError, loop_session_name
from ..model.repositories import GithubFactory, Tracker


class Reviewer:
    """Runs the reviewer from the checkout once the implementation worktree is gone; a read-only gate guards the checkout."""

    def __init__(self, new_agent: AgentLauncher, git: GitClient, phases: Phases) -> None:
        self._new_agent = new_agent
        self._git = git
        self._phases = phases

    def review(
        self, spec: SpecGraphSnapshot, delivery: Delivery, repository: Repository, run_review: ReviewWorkflow
    ) -> ReviewOutcome:
        git, checkout = self._git, repository.checkout
        with self._phases.phase("review", Kind.AGENT, Owner.REVIEWER, spec=spec.number, head=delivery.head_sha) as phase:
            head, status = git.head(checkout), git.status(checkout)
            context = ReviewContext(
                delivery.pull_request_url, checkout, spec.initiative, delivery.head_sha,
                loop_session_name(spec.number, spec.title),
            )
            try:
                with self._new_agent().open() as workspace:
                    result = run_review(context, workspace)
            except (subprocess.CalledProcessError, TicketWorkflowError) as exception:
                phase.fail("Reviewer could not complete")
                return ReviewOutcome(error=f"the reviewer failed: {exception}")
            violation = readonly_violation(git, checkout, head.sha, status)
            if not phase.gate("reviewer left the checkout unchanged", violation is None, violation or ""):
                return ReviewOutcome(error=violation or "")
            phase.gate("every axis completed", result.completed, "an axis did not complete")
            phase.gate("reviewed the current head", result.head_sha == delivery.head_sha, f"reviewed {result.head_sha!r}")
            phase.set(
                findings=len(result.findings), blocking_findings=len(result.blocking_findings), posted=len(result.posted)
            )
        return ReviewOutcome(result)


class Finalizer:
    """Marks the PR ready only when every gate passes and comments the summary on the Spec; never merges or closes the Spec."""

    def __init__(self, git: GitClient, github_for: GithubFactory, tracker: Tracker, phases: Phases) -> None:
        self._git = git
        self._github_for = github_for
        self._tracker = tracker
        self._phases = phases

    def finalize(self, spec: SpecGraphSnapshot, delivery: Delivery, outcome: ReviewOutcome, repository: Repository) -> bool:
        feature_branch = feature_branch_of(spec)
        if feature_branch is None:
            raise TicketWorkflowError(f"cannot resolve feature branch for Spec {spec.url}")
        git = self._git
        with self._phases.phase("finalize", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            git.fetch(repository.checkout)
            current_head = git.ref_sha(repository.checkout, f"origin/{feature_branch}")
            blockers = readiness_blockers(spec, delivery, outcome, current_head)
            phase.gate("PR head is unchanged since the review", current_head == delivery.head_sha, current_head)
            ready = not blockers
            if ready:
                try:
                    PullRequests(self._github_for(repository)).mark_ready(delivery.pull_request_url)
                except subprocess.CalledProcessError as exception:
                    ready = False
                    blockers.append(f"marking the PR ready failed: {exception}")
                    phase.fail(blockers[-1])
            phase.set(ready=ready)
            if not ready:
                phase.fail("PR remains a draft: readiness checks did not pass")
            self._tracker.comment(spec.number, "review-summary", _summary(delivery, outcome, ready, blockers))
        return ready


def readiness_blockers(spec: SpecGraphSnapshot, delivery: Delivery, outcome: ReviewOutcome, current_head: str) -> list[str]:
    """Why the PR must stay a draft; empty when every gate passes."""
    blockers: list[str] = []
    if not delivery.delivered:
        blockers.append("not every Ticket was delivered")
    open_tickets = [ticket for ticket in spec.deliverable_tickets if ticket.state == "open"]
    if open_tickets:
        blockers.append("open Tickets: " + ", ".join(f"#{ticket.number}" for ticket in open_tickets))
    if spec.held_tickets:
        blockers.append("Tickets held (blocked, unread dependencies, or waiting for a human): " + ", ".join(f"#{number}" for number in spec.held_tickets))
    if spec.awaiting_human or any(HITL_LABEL in ticket.labels for ticket in spec.deliverable_tickets):
        blockers.append("a Ticket or the Spec is waiting for a human (`hitl`)")
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


def _summary(delivery: Delivery, outcome: ReviewOutcome, ready: bool, blockers: list[str]) -> str:
    lines = [
        f"implement_spec: pull request {delivery.pull_request_url} is "
        + ("**ready for review**" if ready else "**left as a draft**"),
        "",
        "Commits:",
        *(f"- {commit}" for commit in delivery.commits),
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
