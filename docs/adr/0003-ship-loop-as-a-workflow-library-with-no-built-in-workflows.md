# Ship Loop as a workflow library with no built-in workflows

Loop is a library for composing agent workflows: a workflow is an ordinary Python script that wires a worktree runner, an agent client, and git and GitHub clients, and owns its control flow. The `loop` package ships no workflows; the user writes every workflow, `dev` included, in the harness-root workflows folder on the public `loop` API, which exports the contracts and the shipped implementations so a workflow never reimplements them. This repository keeps a `dev` workflow only as an example and test subject.

Contracts (`abc.ABC`) exist only where Loop's own code calls a replaceable part: `AgentClient`, `ExecutionStore`, `SessionStore`. Clients only a workflow calls (`GitHubClient`) ship as concrete helpers with no contract; a user replaces one by calling their own client from their own workflow, with no dependency on `loop`. The git services (`BranchService`, `WorktreeService`, `CommitService`, [ADR 0010](0010-split-git-access-into-concrete-branch-worktree-and-commit-services.md)) are also concrete even though Loop's own run lifecycle calls them: only tests replace git, and fakes subclass the concrete services.

## Considered Options

- **A platform-neutral `WorkTracker` ABC that `GitHubClient` inherits** — rejected: the workflow is the only caller and is user-owned, so the contract constrains nothing.
- **A `typing.Protocol` for the tracker** — rejected: same reason; a structural type nobody but the workflow checks adds surface without value.

- **Ship built-in workflows (`dev`) inside the `loop` package, resolved before the harness folder** — rejected: workflows are user-owned; repository-specific settings and hooks belong in the user's file, and a built-in `dev` would need an override channel back into it.
- **Expose only `loop.runtime` to user-supplied workflows; they bring their own adapters** — rejected: every workflow needing GitHub or Copilot access reimplements the clients, while the prot2 prototype shows workflows composing the shipped worktree runner, clients, and stores directly.

## Consequences

- A `pip install` alone yields no runnable Workflow and no command; a Workflow runs only once the user has a script such as `workflows/dev.py` and their own shell alias for it (e.g. `alias loop-dev='python workflows/dev.py'`).
- The repository's example `dev` workflow is not part of the compatibility surface.

- The shipped implementations (worktree runner, git and GitHub clients, agent client, stores) are part of the compatibility surface; breaking them breaks user-supplied workflows.
- Importing anything other than the public `loop` API from a workflow is a violation.
- Running a workflow file executes its code; the harness workflows folder is trusted by the act of placing a file there.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how workflows are composed and run. Loop ships no `loop <name>` dispatch command; this supersedes the command decision of [ADR 0002](0002-ship-loop-as-the-loop-package-with-an-loop-command.md).
