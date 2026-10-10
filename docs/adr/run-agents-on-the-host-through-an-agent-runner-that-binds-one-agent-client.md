# Run agents on the host through an agent runner that binds one agent client

> Superseded by [Build agents with an agent builder over profiles, branch strategies, and session stores](build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md).

A worktree serves one or more agent runs, and the worktree, its agent client, and their disposal need one owner. Loop runs every agent directly on the host, from the harness root, with the user's own permissions; there is no Sandbox, container, or isolation layer. An `AgentRunnerProvider` creates the worktree, runs the `worktree-ready` hooks, builds one `AgentClient` from the `AgentClientFactory` with the worktree-bound executor, and returns an `AgentRunner` that owns a `WorktreeLifecycle` and that client. `AgentRunner.run` runs the bound client; leaving the runner's `with` block calls `AgentClient.exit()` and then disposes the worktree, so the worktree lives until all tasks are processed.

## Considered Options

- **A Sandbox contract with `NoSandbox` and `DockerSandbox` implementations** — rejected: the container path carried its own image, mount, UID, retry, and git-identity machinery for isolation Loop does not need, and the host path had only one real implementation behind the contract.
- **A workflow calls the agent client directly on the host with no runner** — rejected: worktree creation, base-head capture, commit collection, and cancellation cleanup would be repeated in every workflow.
- **A `WorktreeRunner` that takes the agent on each `run(agent, ...)`** — rejected: the worktree, the client, and its disposal had no single owner, and the client could not be disposed with the worktree.
- **A runner bound to one agent client built from `agent_factory` at construction, without a provider** — rejected: the same ownership gap; the provider also keeps worktree creation and hooks in one place.

## Consequences

- Agents have no isolation from the host; path-scoped permissions (`--allow-all-tools` with `--add-dir`, [Run Copilot CLI agents from the harness root](run-copilot-cli-agents-from-the-harness-root-with-harness-and-workspace-isolation.md)) are the only boundary.
- `AgentClientFactory` takes an `AgentBinding` (executor and harness-root workspace); the executor defaults to running commands on the host in the harness root and can be injected for tests.
- `worktree-ready` is the only Loop hook point; there is no `sandbox-ready` stage or `apply_to_host` step.
- Crew agents (Codey, then Chorey) on one worktree need one runner each or a new worktree.
- `run` returns the session key used; passing it back in the run options continues that conversation, `new_session=True` starts a resumable one, and no key keeps the run stateless.
- Close-ticket and pull-request steps stay in the workflow.
- Removing `Sandbox`, `WorktreeRunner`, `WorktreeRunResult`, and `create_worktree_runner` is a breaking change for user-supplied workflows ([Ship Loop as a workflow library](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).

See [Agent Client](../concepts/str-agent-client.md) and [Agent Run](../concepts/str-agent-run.md) for how it is applied.