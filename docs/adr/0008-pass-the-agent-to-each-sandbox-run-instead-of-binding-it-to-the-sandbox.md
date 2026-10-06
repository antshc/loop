# Pass the agent to each Sandbox run instead of binding it to the Sandbox

A Sandbox is built once per worktree and serves one or more agent runs, but it was bound to a single agent client through an `agent_factory` given at construction. Loop makes a Sandbox only the environment — workspace, `exec`, `close`, and the executor agents run through — and the workflow passes the agent on each run (`sandbox.run(agent, prompt, ...)`), as Sandcastle passes the agent to each `sandbox.run()`. `NoSandbox` and `DockerSandbox` both drop `agent_factory`; a `NoSandbox` run executes the agent on the host.

## Considered Options

- **A Sandbox owns one agent client built from `agent_factory` at construction (the previous shape)** — rejected: one shared Sandbox can run only one agent, so Crew agents (for example Codey, then Chorey) on one worktree need separate Sandboxes or a shared agent.
- **`NoSandbox` is removed from the Sandbox contract and a workflow calls the agent client directly on the host** — rejected: it breaks the rule that every agent invocation goes through a Sandbox and splits the run lifecycle by isolation kind.
- **Only `NoSandbox` takes a ready agent client while `DockerSandbox` keeps `agent_factory`** — rejected: the two Sandboxes would no longer share one construction and run shape.

## Consequences

- `AgentClientFactory` leaves the Sandbox constructors, and the Sandbox hands each run's agent its own executor (the host's for `NoSandbox`, `docker exec` for `DockerSandbox`).
- The Sandbox signature is part of the compatibility surface for user-supplied workflows ([ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)); this is a breaking change to it.

See [Agent Client](../concepts/str-agent-client.md) and [Sandbox Run](../concepts/str-sandbox-run.md) for how it is applied.
