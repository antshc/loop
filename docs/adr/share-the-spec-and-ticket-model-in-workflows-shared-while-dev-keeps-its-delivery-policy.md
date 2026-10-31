# Share the Spec and Ticket model in Loop's shared GitHub package while implement_spec keeps its delivery policy

**Superseded by [Keep GitHub integration in the factory](keep-github-integration-in-the-factory.md).**

[Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) keeps Workflow-specific models inside their Workflow, and `Spec` and `Ticket` began as `implement_spec` records. They live in Loop's shared GitHub package (`src/shared/github/`, imported as `loop.shared.github`) on top of the shared `Issue` model, together with their metadata (Initiative id, target repository, base branch), classification, readiness and ticketing, so every Workflow that works Specs reuses one model. `implement_spec` keeps its delivery policy: the `hitl` hand-off and escalation, its comment text, branch naming, failure counts, and the Ticket, plan and review result types. Applies only to `Spec` and `Ticket`.

## Considered Options

- **Keep `Spec` and `Ticket` in `implement_spec`** — rejected: a second Workflow working Specs would import `implement_spec` or duplicate the model.
- **Move `Spec` and `Ticket` unchanged, including `implement_spec`'s `hitl` hand-off and escalation behavior** — rejected: sibling Workflows would inherit `implement_spec`'s escalation rules.
- **Keep the GitHub package Workflow-side, in the factory's shared infrastructure** (the previous home, `software_factory/src/shared/github/`) — rejected: the model, clients and pure policies mean the same to every Workflow, so Loop ships them once beside its other reusable helpers.

## Consequences

- This is a deliberate exception to the rule that shared code holds only logic whose business meaning is identical across Workflows; other Workflow-specific models stay in their Workflow.
- `implement_spec` Stages depend on Loop's `Spec` and `Ticket` records, and their escalation and publication rules act on those records from inside the factory.
- The GitHub clients remain concrete helpers a Workflow calls; Loop's runtime never imports them.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how Workflows and the shared helpers are composed.
