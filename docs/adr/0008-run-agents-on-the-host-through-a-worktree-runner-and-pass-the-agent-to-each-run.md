# Run agents on the host through a worktree runner and pass the agent to each run

A worktree is built once and serves one or more agent runs. Loop runs every agent directly on the host, from the harness root, with the user's own permissions, through a `WorktreeRunner` that owns the worktree and its lifecycle; there is no Sandbox, container, or isolation layer. The workflow passes the agent on each run (`runner.run(agent, prompt, ...)`), and the runner hands that agent the executor it runs through.

## Considered Options

- **A Sandbox contract with `NoSandbox` and `DockerSandbox` implementations (the previous shape)** — rejected: the container path carried its own image, mount, UID, retry, and git-identity machinery for isolation Loop does not need, and the host path had only one real implementation behind the contract.
- **A runner bound to one agent client built from `agent_factory` at construction** — rejected: one shared worktree could run only one agent, so Crew agents (for example Codey, then Chorey) on one worktree would need separate runners or a shared agent.
- **A workflow calls the agent client directly on the host with no runner** — rejected: worktree creation, base-head capture, commit collection, and cancellation cleanup would be repeated in every workflow.

## Consequences

- Agents have no isolation from the host; path-scoped permissions (`--allow-all-tools` with `--add-dir`, [ADR 0005](0005-run-copilot-cli-agents-from-the-harness-root-with-harness-and-workspace-isolation.md)) are the only boundary.
- `AgentClientFactory` takes an `AgentBinding` (executor and harness-root workspace); the executor defaults to running commands on the host in the harness root and can be injected for tests.
- `worktree-ready` is the only Hook point; there is no `sandbox-ready` stage or `apply_to_host` step.
- The runner signature is part of the compatibility surface for user-supplied workflows ([ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)); removing `Sandbox`, `NoSandbox`, `DockerSandbox`, and `create_sandbox` is a breaking change to it.

See [Agent Client](../concepts/str-agent-client.md) and [Agent Run](../concepts/str-agent-run.md) for how it is applied.
