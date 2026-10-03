# Expose only the runtime to user-supplied workflows

Workflows are plugins: built-in ones ship inside the `shipyard` package, and further ones are discovered and loaded from directories the user supplies. Code outside the package needs a fixed surface to build on, so user-supplied workflows may import only `shipyard.runtime` (contracts, run context, policies) and bring their own adapters; the shared `shipyard.adapters` package stays internal to built-in workflows.

## Considered Options

- **Expose `shipyard.runtime` and `shipyard.adapters` to user-supplied workflows** — rejected: freezes the shared adapters and the process helper as a compatibility surface for code outside the package.
- **Ship no built-in workflows; load every workflow, `dev` included, from user-supplied directories** — rejected: a `pip install` must yield a working `ship dev` (ADR 0001).

## Consequences

- A user-supplied workflow that needs GitHub or Copilot access implements those runtime contracts itself, duplicating built-in adapters.
- Loading a workflow executes its code; user-supplied directories are trusted by the act of configuring them.
- Breaking changes to `shipyard.runtime` break user-supplied workflows; shared adapters can change freely.

See [CLI Runtime Workflow Architecture](../concepts/str-cli-runtime-workflow-architecture.md) for discovery and composition.
