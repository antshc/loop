# Run Copilot CLI agents from the harness root with harness-and-workspace isolation

> Superseded by [Build agents with an agent builder over profiles, branch strategies, and session stores](build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md).

Copilot CLI agents need the harness `.github` skills/instructions and `docs` while changing code in isolated worktrees. Loop launches the agent from the harness repository and defines the run's isolation boundary to include the harness plus the relevant workspace repositories and worktrees, so harness context remains available in both single-repo and multi-repo layouts. The agent runs on the host with path-scoped permissions (`--allow-all-tools` with `--add-dir`), so its boundary stays the harness and the relevant repositories and worktrees.

## Considered Options

- **`--allow-all` as the default** — rejected: it disables path verification, so the agent could read and write anywhere on the host and the isolation boundary above would no longer hold.
- **`--allow-all` forced for every run, even when the workflow passes its own flags** — rejected: it reverses this decision's host boundary, and only `--deny-tool` and `--deny-url` would still limit the agent.
- **Single Repo worktrees beside the harness root, outside it** — rejected: with the harness root as the execution root and the agent workspace, a worktree outside it needs extra `--add-dir` grants.
- **Run Copilot CLI from the target worktree** — rejected: the target worktree would become the execution root, so harness `.github` skills/instructions and `docs` would no longer be the stable agent context.
- **Use a worktree inside the harness workspace as the Copilot execution root** — rejected: colocating the worktree under the harness does not preserve the harness repository itself as the execution root; the agent still needs the harness `.github` and `docs` as its primary context.

## Consequences

- Agent commands that modify code must address the target worktree explicitly because the process starts from the harness root.
- Isolation must admit the harness repository and the relevant workspace repositories/worktrees for the run.
- The provider adapter owns the permission flags; there is a single host mode (see [Run agents through an agent runner](run-agents-on-the-host-through-an-agent-runner-that-binds-one-agent-client.md)).
- Worktrees live under the harness's `workspace` folder in both layouts, as `<harness>/workspace/<checkout folder name>.worktrees/<branch>`, so the harness root contains every worktree; creating one adds `workspace/` to the checkout's `.git/info/exclude`.
- Harness `.github` customizations and documentation stay available without copying them into each worktree.

See [Harness Repository Topology](../concepts/str-harness-repository-topology.md) for how it is applied.
