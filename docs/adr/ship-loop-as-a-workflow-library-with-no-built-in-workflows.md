# Ship Loop as a workflow library with no built-in workflows

Loop is a library for composing agent workflows: a workflow is an ordinary Python script that wires an agent runner, git services, and GitHub client, and owns its control flow. The Python package is named `loop` and ships no command and no workflows; the user writes every workflow, `dev` included, in the harness-root workflows folder on the public `loop` API, which exports the contracts and the shipped implementations so a workflow never reimplements them. This repository keeps a `dev` workflow only as an example and test subject.

Contracts (`abc.ABC`) exist only where Loop's own code calls a replaceable part: `AgentClient`, `ExecutionStore`, `SessionStore`. Clients only a workflow calls (`GitHubClient`) ship as concrete helpers with no contract; a user replaces one by calling their own client from their own workflow, with no dependency on `loop`. The git services (`BranchService`, `WorktreeService`, `CommitService`, [Split git access](split-git-access-into-concrete-branch-worktree-and-commit-services.md)) are also concrete even though Loop's own run lifecycle calls them: only tests replace git, and fakes subclass the concrete services.

## Considered Options

- **A platform-neutral `WorkTracker` ABC that `GitHubClient` inherits** — rejected: the workflow is the only caller and is user-owned, so the contract constrains nothing.
- **A `typing.Protocol` for the tracker** — rejected: same reason; a structural type nobody but the workflow checks adds surface without value.
- **A single `loop` console command whose subcommands are the discovered workflows (`loop dev --option <value>`)** — rejected: a dispatch command needs workflow discovery and a registry contract, while a plain script run through the user's own alias needs neither.
- **Keep the `afk` import package, with a `ralph` or `afk` console command, or one console script per workflow (`afk_dev`, `afk_fix_prs`, `afk_address_prs`)** — rejected: Ralph is the orchestrator, not the product, and the names do not match the product Loop.
- **Ship built-in workflows (`dev`) inside the `loop` package, resolved before the harness folder** — rejected: workflows are user-owned; repository-specific settings and hooks belong in the user's file, and a built-in `dev` would need an override channel back into it.
- **Expose only `loop.runtime` to user-supplied workflows; they bring their own adapters** — rejected: every workflow needing GitHub or Copilot access reimplements the clients, while the prot2 prototype shows workflows composing the shipped worktree runner, clients, and stores directly.

## Consequences

- A `pip install` alone yields no runnable Workflow and no command; a Workflow runs only once the user has a script such as `workflows/dev.py` and their own shell alias for it (e.g. `alias loop-dev='python workflows/dev.py'`).
- The repository's example `dev` workflow is not part of the compatibility surface.
- The import path, `pyproject.toml` project name, and any `afk_*` invocations in skills and docs follow the `loop` package name.
- The shipped implementations (agent runner, git services, agent client, stores) are part of the compatibility surface; breaking them breaks user-supplied workflows.
- Importing anything other than the public `loop` API from a workflow is a violation.
- Running a workflow file executes its code; the harness workflows folder is trusted by the act of placing a file there.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how workflows are composed and run.
