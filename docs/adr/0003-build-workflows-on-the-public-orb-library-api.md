# Build workflows on the public `orb` library API

Orb is a library for composing agent workflows: a workflow is an ordinary Python script that wires a Capsule, an agent client, and git and GitHub clients, and owns its control flow. Built-in and user-supplied workflows import only the public `orb` package API, which exports both the contracts and the shipped implementations, so a workflow never reimplements them.

## Considered Options

- **Expose only `orb.runtime` to user-supplied workflows; they bring their own adapters** — rejected: every workflow needing GitHub or Copilot access reimplements the clients, while the prot2 prototype shows workflows composing the shipped Capsules, clients, and stores directly.
- **Ship no built-in workflows; load every workflow, `dev` included, from the user's workflows folder** — rejected: a `pip install` must yield a working `orb dev` (ADR 0001).

## Consequences

- The shipped implementations (Capsules, git and GitHub clients, agent client, stores) are part of the compatibility surface; breaking them breaks user-supplied workflows.
- Importing anything other than the public `orb` API from a workflow is a violation.
- Running a workflow file executes its code; the harness workflows folder is trusted by the act of placing a file there.

See [Orb Library Workflow Architecture](../concepts/str-orb-library-workflow-architecture.md) for how workflows are discovered and composed.
