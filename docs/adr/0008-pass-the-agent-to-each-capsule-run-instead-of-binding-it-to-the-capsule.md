# Pass the agent to each Capsule run instead of binding it to the Capsule

A Capsule is built once per worktree and serves one or more agent runs, but it was bound to a single agent client through an `agent_factory` given at construction. Orb makes a Capsule only the environment — workspace, `exec`, `close`, and the executor agents run through — and the workflow passes the agent on each run (`capsule.run(agent, prompt, ...)`), as Sandcastle passes the agent to each `sandbox.run()`. `NoCapsule` and `DockerCapsule` both drop `agent_factory`; a `NoCapsule` run executes the agent on the host.

## Considered Options

- **A Capsule owns one agent client built from `agent_factory` at construction (the previous shape)** — rejected: one shared Capsule can run only one agent, so Crew agents (for example Codey, then Chorey) on one worktree need separate Capsules or a shared agent.
- **`NoCapsule` is removed from the Capsule contract and a workflow calls the agent client directly on the host** — rejected: it breaks the rule that every agent invocation goes through a Capsule and splits the run lifecycle by isolation kind.
- **Only `NoCapsule` takes a ready agent client while `DockerCapsule` keeps `agent_factory`** — rejected: the two Capsules would no longer share one construction and run shape.

## Consequences

- `AgentClientFactory` leaves the Capsule constructors, and the Capsule hands each run's agent its own executor (the host's for `NoCapsule`, `docker exec` for `DockerCapsule`).
- The Capsule signature is part of the compatibility surface for user-supplied workflows ([ADR 0003](0003-ship-orb-as-a-workflow-library-with-no-built-in-workflows.md)); this is a breaking change to it.

See [Agent Client](../concepts/str-agent-client.md) and [Capsule Run](../concepts/str-capsule-run.md) for how it is applied.
