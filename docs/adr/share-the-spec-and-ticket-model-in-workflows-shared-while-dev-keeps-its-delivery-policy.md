# Share the Spec and Ticket model in workflows/shared while dev keeps its delivery policy

[Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) keeps workflow-specific models inside their slice, and `Spec` and `Ticket` began as `implement_spec` records. They now live in `workflows/shared` on top of the shared GitHub `Issue` model, together with their metadata (Initiative id, target repository, base branch), classification, readiness, and ticketing, so every Workflow that works Specs reuses one model. `implement_spec` keeps its delivery policy: the `hitl` hand-off and escalation, its comment text, branch naming, failure counts, and the Ticket, plan, and review result types. Applies only to `Spec` and `Ticket`.

## Considered Options

- **Keep `Spec` and `Ticket` in `workflows/implement_spec`** — rejected: a second Workflow working Specs would import `implement_spec` or duplicate the model.
- **Move `Spec` and `Ticket` unchanged, including `implement_spec`'s `hitl` hand-off and escalation behavior** — rejected: sibling Workflows would inherit `implement_spec`'s escalation rules.

## Consequences

- This is a deliberate exception to the rule that `workflows/shared` holds only logic whose business meaning is identical across Workflows; other Workflow-specific models stay in their slice.
- `implement_spec` stages depend on shared `Spec` and `Ticket` records, and their escalation and publication rules act on those records from inside `implement_spec`.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how Workflows and `workflows/shared` are composed.
