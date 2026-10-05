# Run Copilot CLI agents from the harness root with harness-and-workspace isolation

Copilot CLI agents need the harness `.github` skills/instructions and `docs` while changing code in isolated worktrees. Orb launches the agent from the harness repository and defines the run's isolation boundary to include the harness plus the relevant workspace repositories and worktrees, so harness context remains available in both single-repo and multi-repo layouts.

## Considered Options

- **Run Copilot CLI from the target worktree** — rejected: the target worktree would become the execution root, so harness `.github` skills/instructions and `docs` would no longer be the stable agent context.
- **Use a worktree inside the harness workspace as the Copilot execution root** — rejected: colocating the worktree under the harness does not preserve the harness repository itself as the execution root; the agent still needs the harness `.github` and `docs` as its primary context.

## Consequences

- Agent commands that modify code must address the target worktree explicitly because the process starts from the harness root.
- Isolation must admit the harness repository and the relevant workspace repositories/worktrees for the run.
- Harness `.github` customizations and documentation stay available without copying them into each worktree.

See [Harness Repository Topology](../concepts/str-harness-repository-topology.md) for how it is applied.
