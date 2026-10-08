# Bind one agent client to each agent runner created by a provider

Supersedes the per-run agent rule of [ADR 0008](0008-run-agents-on-the-host-through-a-worktree-runner-and-pass-the-agent-to-each-run.md); agents still run on the host with no isolation layer. An `AgentRunnerProvider` creates the worktree, runs the `worktree-ready` hooks, builds one `AgentClient` from the `AgentClientFactory` with the worktree-bound executor, and returns an `AgentRunner` that owns a `WorktreeLifecycle` and that client. `AgentRunner.run(prompt, args, options, session_id)` runs the bound client; leaving the runner's `with` block calls `AgentClient.exit()` and then disposes the worktree, so the worktree lives until all tasks are processed.

## Considered Options

- **Keep `WorktreeRunner.run(agent, ...)` with the agent passed on each run (ADR 0008)** — rejected: the worktree, the client, and its disposal had no single owner, and the client could not be disposed with the worktree.
- **A runner bound to one agent client at creation** — chosen: one resource owns both and disposes both; Crew agents (Codey, then Chorey) on one worktree need one runner each or a new worktree.

## Consequences

- `WorktreeRunner`, `WorktreeRunResult`, and `create_worktree_runner` are removed; this is a breaking change for user-supplied workflows ([ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- `run` returns the session id used; passing it back continues that conversation, `new_session=True` starts a resumable one, and no id keeps the run stateless as in ADR 0009.
- Close-ticket and pull-request steps stay in the workflow.
