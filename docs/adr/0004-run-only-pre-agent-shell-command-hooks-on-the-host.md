# Run only pre-agent shell-command hooks on the host and fail the run on error

Repositories need to prepare a fresh worktree before the agent starts, and Sandcastle's hook model shows what is enough. Orb supports user-declared shell commands at a `worktree-ready` Hook point, executed on the host before the agent, and a hook that exits non-zero or times out fails the run.

## Considered Options

- **Post-agent hooks** — rejected: Sandcastle shows pre-agent hooks suffice; post-iteration work is an explicit step of the workflow's use case.
- **Capsule-side hooks** — rejected: Orb runs agents in host worktrees, so there is no Capsule to run them in.
- **In-language callbacks instead of shell commands** — rejected: ties hook authors to Python and widens the runtime surface exposed to user-supplied workflows (ADR 0003); shell commands stay language-neutral.
- **Warn and continue on hook failure** — rejected: the agent would run against a half-prepared worktree and spend a session on a setup error.

## Consequences

- A broken setup command stops the run before any agent time is spent.
- Verification, cleanup, and notification after the agent must be workflow steps, not hooks.
- Hooks run unsandboxed with the user's privileges, so configuring one is trusted by the act of configuring it.
- Further Hook points can be added without changing the declaration shape.

See [Lifecycle Hooks](../concepts/str-lifecycle-hooks.md) for how it is applied.
