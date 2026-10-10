# Build agents with an agent builder over profiles, branch strategies, and session stores

Workflows need one composable way to run an agent CLI, optionally on a Git worktree, optionally in Docker, optionally continuing a session, without the library knowing Tickets or prompts. The `loop` package adopts a builder: `Agent()` returns an `AgentBuilder` that is configured with `with_git`, `with_docker`, `with_session`, and `with_agent_cli_hooks`, then `create()` or `open()` yields an `AgentClient`, or a `Worktree` that hands out clients bound to it. The CLI, model, and reasoning effort are an `AgentProfile` passed per client, each run takes one `AgentRequest(prompt)`, and the CLI's raw stdout is the `AgentResult.output`.

## Considered Options

- **Keep the agent runner, concrete Branch/Worktree/Commit services, and the output parser** — rejected: three overlapping lifecycles, a library-owned response envelope, and a cancellable executor for features the workflows do not need; every Workflow-specific need (response envelope, commit validation, publication) belongs in the Workflow.
- **One flat client class with options** — rejected: worktree, Docker, session, and hook concerns compose, so a decorator stack built per client keeps each concern optional and independently replaceable.
- **Builder over profiles and strategies (chosen)** — a `GitOptions` carries the worktree root, repository path, a `BranchStrategy` (a branch off a base, reused when it already exists locally) or a `MergeToHeadStrategy`/`HeadStrategy`, and the Loop hooks; `CliRunner`, `GitService`, and `DockerService` are the test seams, so tests run only against fakes.

## Consequences

- The agent's working directory is the worktree, not the harness root. This supersedes [Run Copilot CLI agents from the harness root](run-copilot-cli-agents-from-the-harness-root-with-harness-and-workspace-isolation.md): a target repository that is not the harness no longer sees the harness `.github` customizations unless the Workflow passes an `AgentContext(cwd=..., add_dirs=(harness,))` to a run (`--add-dir`); the example Workflows do not.
- The `GitOptions` root path must sit inside the process working directory, so a Workflow that keeps worktrees under the harness `workspace` folder is run from the harness root.
- Existing local branches are reused as they are, not reset; the strategy fetches the repository on open and makes a safety-net commit of leftover changes before removal, because git refuses to remove a dirty worktree. A failed `MergeToHeadStrategy` run keeps its temporary branch unmerged.
- The library returns raw stdout and raises `CalledProcessError` on a non-zero exit; each Workflow extracts and validates its own response envelope.
- Everything a Workflow needs beyond that (push, commit validation, Ticket state, failure counts, the dev result model, prompt rendering) lives under `workflows/`, not in `loop`.
- Live output streaming, per-run timeouts, and cancellation are not provided; only `plan_implement` handles `KeyboardInterrupt`.
- Loop hooks run on the host at `worktree-ready` (before the agent), `worktree-removing` (after the safety-net commit, before removal), and `run-finished` (in the repository after the merge, only on success); a failing hook raises `LoopHookError`. Agent CLI hooks are observe-only.

See [Agent Run](../concepts/str-agent-run.md) and [Agent Client](../concepts/str-agent-client.md) for how it is applied.
