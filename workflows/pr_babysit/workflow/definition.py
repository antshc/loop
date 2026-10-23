from __future__ import annotations

from loop import Kind, Workflow, WorkflowBuilder


def babysit_workflow(*, observer=None, visualize: bool = False) -> Workflow:
    """Declare only meaningful PR maintenance phases for reporting."""
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
