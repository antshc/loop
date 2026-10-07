# Split git access into concrete Branch, Worktree, and Commit services

`GitClient` had grown into a shallow wrapper whose callers (the `dev` Workflow and the Sandbox lifecycle) chose start refs, checked local and remote ref presence, and handled raw HEAD hashes. Git access is now three concrete deep modules on the public `loop` API — `BranchService` (fetch, prepare, delete, push, merge, ahead of remote), `WorktreeService` (create, list, remove, detach, clean/changes), and `CommitService` (head, since, find since, restore) — over self-locating `Branch`, `Worktree`, and `Commit` entities that carry their repository `path`. Callers state intent; ref selection, presence checks, and git command choice stay inside the services. `GitClient` stops being public and survives only as the internal command runner and lock.

## Considered Options

- **Keep `GitClient` public as a facade holding the three services and the leftover operations** — rejected: it keeps a second public path to the same behavior and leaves the shallow wrapper on the compatibility surface.
- **`typing.Protocol` or `abc.ABC` contracts for the services** — rejected: only tests replace git, and fakes subclassing the concrete services cover that ([ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- **Path-free entities with every service method taking a `checkout`/`worktree` path** — rejected: lifecycle code carries checkout and worktree paths side by side and branch conflicts need a compare-by-name special case.
- **One service instance bound per repository** — rejected: the dev Workflow and Sandbox lifecycle work across checkout and worktree in one flow, so per-repository instances multiply wiring.

## Consequences

- `Branch` equality is by `name` only; its `path` is the checkout or worktree commands run in, so one branch seen from two paths compares equal.
- `BranchService.prepare` never force-resets a branch already checked out at the intended worktree, so unpublished commits left by a cancelled run survive the rerun.
- `WorktreeService.create` attaches a prepared branch at any target path without checking it; worktree placement and the `workspace/` exclude are caller policy, applied by `create_sandbox`'s default path ([ADR 0005](0005-run-copilot-cli-agents-from-the-harness-root-with-harness-and-workspace-isolation.md)).
- The `ccode(...)` subject prefix is a `dev` Workflow convention and leaves the library ([ADR 0009](0009-run-one-fresh-agent-per-ticket-from-python-and-let-the-agent-commit-it.md)).
- The git test double is one shared in-memory `FakeGit` exposing fake branch, worktree, and commit services, so an agent stand-in's commit is visible to every service.
- Replacing `GitClient` with the services is a breaking change for user Workflows.
