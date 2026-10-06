# Ship Orb as a workflow library with no built-in workflows

Orb is a library for composing agent workflows: a workflow is an ordinary Python script that wires a Capsule, an agent client, and git and GitHub clients, and owns its control flow. The `orb` package ships no workflows; the user writes every workflow, `dev` included, in the harness-root workflows folder on the public `orb` API, which exports the contracts and the shipped implementations so a workflow never reimplements them. This repository keeps a `dev` workflow only as an example and test subject.

Contracts (`abc.ABC`) exist only where Orb's own code calls a replaceable part: `Capsule`, `AgentClient`, `ExecutionStore`, `SessionStore`. Clients only a workflow calls (`GitClient`, `GitHubClient`) ship as concrete helpers with no contract; a user replaces one by calling their own client from their own workflow, with no dependency on `orb`.

## Considered Options

- **A platform-neutral `WorkTracker` ABC that `GitHubClient` inherits** — rejected: the workflow is the only caller and is user-owned, so the contract constrains nothing.
- **A `typing.Protocol` for the tracker** — rejected: same reason; a structural type nobody but the workflow checks adds surface without value.

- **Ship built-in workflows (`dev`) inside the `orb` package, resolved before the harness folder** — rejected: workflows are user-owned; repository-specific settings and hooks belong in the user's file, and a built-in `dev` would need an override channel back into it.
- **Expose only `orb.runtime` to user-supplied workflows; they bring their own adapters** — rejected: every workflow needing GitHub or Copilot access reimplements the clients, while the prot2 prototype shows workflows composing the shipped Capsules, clients, and stores directly.

## Consequences

- A `pip install` alone yields no runnable Workflow and no command; a Workflow runs only once the user has a script such as `workflows/dev.py` and their own shell alias for it (e.g. `alias orb-dev='python workflows/dev.py'`).
- The repository's example `dev` workflow is not part of the compatibility surface.

- The shipped implementations (Capsules, git and GitHub clients, agent client, stores) are part of the compatibility surface; breaking them breaks user-supplied workflows.
- Importing anything other than the public `orb` API from a workflow is a violation.
- Running a workflow file executes its code; the harness workflows folder is trusted by the act of placing a file there.

See [Orb Library Workflow Architecture](../concepts/str-orb-library-workflow-architecture.md) for how workflows are composed and run. Orb ships no `orb <name>` dispatch command; this supersedes the command decision of [ADR 0002](0002-ship-orb-as-the-orb-package-with-an-orb-command.md).
