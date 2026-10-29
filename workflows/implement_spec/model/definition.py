"""Workflow-owned phase declarations; Python helpers own their execution."""

from __future__ import annotations

from typing import Any

from loop import Kind, Role, Workflow, WorkflowBuilder


class Owner:
    """Names the actors exported as Aspire resources."""

    ENGINEER = Role.ENGINEER
    CODE = Role.CODE
    PLANNER = Role.PLANNER
    DEVELOPER = Role.DEVELOPER
    CHORE = "chore"
    REVIEWER = Role.REVIEWER


def implement_spec_workflow(*, observer: Any = None, visualize: bool = False) -> Workflow:
    """Builds high-level implement_spec reporting with optional terminal and OTLP views."""
    builder = (
        WorkflowBuilder("implement_spec")
        .phase("spec", "Spec", Kind.ENGINEER, Owner.ENGINEER)
        .phase("preflight", "Prepare repository", Kind.CODE, Owner.CODE)
        .phase("plan", "Plan tickets", Kind.AGENT, Owner.PLANNER)
        .phase("create-tickets", "Create tickets", Kind.CODE, Owner.CODE)
        .phase("deliver", "Deliver tickets", Kind.CODE, Owner.CODE)
        .phase("ticket", "Implement ticket", Kind.AGENT, Owner.DEVELOPER)
        .phase("validate", "Validate ticket commit", Kind.CODE, Owner.CODE, visible=False)
        .phase("close-ticket", "Close ticket", Kind.CODE, Owner.CODE, visible=False)
        .phase("restore", "Restore rejected changes", Kind.CODE, Owner.CODE, visible=False)
        .phase("escalate", "Request human input", Kind.CODE, Owner.CODE)
        .phase("publish", "Publish draft PR", Kind.CODE, Owner.CODE)
        .phase("chore", "Refine changes", Kind.AGENT, Owner.CHORE)
        .phase("commit-chore", "Commit cleanup", Kind.CODE, Owner.CODE, visible=False)
        .phase("review", "Review pull request", Kind.AGENT, Owner.REVIEWER)
        .phase("finalize", "Check readiness", Kind.CODE, Owner.CODE)
        .console()
    )
    if visualize:
        builder.visualize()
    if observer is not None:
        builder.otel(observer)
    return builder.build()
