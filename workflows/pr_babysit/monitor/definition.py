"""The babysit Workflow: only meaningful PR maintenance phases are reported."""

from __future__ import annotations

from loop import Kind, RunObserver, Workflow, WorkflowBuilder


def babysit_workflow(*, observer: RunObserver | None = None, visualize: bool = False) -> Workflow:
    """Declares the reportable phases; quiet polls never open one."""
    builder = (WorkflowBuilder("babysit")
               .phase("prepare", "Prepare repository", Kind.CODE, "code")
               .phase("address", "Address review feedback", Kind.AGENT, "address")
               .phase("diagnose", "Diagnose CI failure", Kind.AGENT, "maintainer")
               .phase("fix-ci", "Fix CI failure", Kind.AGENT, "maintainer")
               .phase("verify", "Verify changes", Kind.CODE, "code")
               .phase("publish", "Publish changes and replies", Kind.CODE, "code")
               .phase("ready", "PR readiness changed", Kind.CODE, "code")
               .console())
    if visualize:
        builder.visualize()
    if observer is not None:
        builder.otel(observer)
    return builder.build()
