# Keep GitHub integration in the factory

GitHub access currently combines provider operations with factory conventions: Spec and Ticket roles, labels, routing, dependency readiness, trusted feedback and PR readiness. Projects can use different conventions, so the integration belongs to their editable factory code.

## Decision

Move the entire GitHub package to `workflows/software_factory/infrastructure/github/`, preserving its internal structure and behavior. Factory workflows import it directly and customize ordinary Python. Loop retains generic Git and process helpers; its wheel contains no GitHub package. No old import wrappers or policy configuration framework are added.

This supersedes [Share the Spec and Ticket model in Loop](share-the-spec-and-ticket-model-in-workflows-shared-while-dev-keeps-its-delivery-policy.md) and the GitHub placement in [Ship Loop as a workflow library](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md).

## Consequences

- GitHub tests, builders and boundary fakes move under factory infrastructure tests.
- Pure models, events and policies remain independent of GitHub clients and process execution.
- Factory entry points use a factory ledger reader that normalizes old GitHub type tags in memory. Existing append-only records remain unchanged; the generic Loop codec gains no GitHub knowledge.
- Existing labels, readiness implementations, trust rules and API behavior remain unchanged.
