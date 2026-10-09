# Pass the model and reasoning effort as run arguments, not agent options

One agent runner holds one agent client ([ADR 0011](0011-bind-one-agent-client-to-each-agent-runner-created-by-a-provider.md)), yet Runs on the same worktree need different models and reasoning effort — for example, Opus at `max` to plan and Sonnet at `high` to implement. So `AgentRunner.run` and `AgentClient.run` take optional `model` and `reasoning_effort` strings, placed before the also-optional `options`, and `AgentOptions` no longer carries a model; omitting either leaves the provider default. The provider adapter maps them to its flags (`--model`, `--reasoning-effort` for Copilot CLI) and validates neither: the provider rejects an unknown model or an unsupported effort.

## Considered Options

- **Keep `model` in `AgentOptions`** — rejected: the model is the choice that distinguishes Runs on one runner, so it is stated on each run call next to the prompt rather than inside the options bag.
- **Add `reasoning_effort` to `AgentOptions`** — rejected: effort is chosen per Run together with the model.
- **Set a default model when the agent client is created (`copilot(sessions, model=...)`)** — rejected: one client serves Runs with different models, so a client-level model would be overridden on nearly every run.
- **Add `model=` to `run` and keep `AgentOptions.model` as well** — rejected: two ways to set one value.
- **Make `model` required** — rejected: the provider default is a valid choice, and existing workflows keep running unchanged.

## Consequences

- Removing `AgentOptions.model` and changing the `run` signatures breaks user-supplied workflows that set it or pass `options` positionally ([ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- Loop does not know which models a provider offers or which efforts each supports; see [Copilot CLI models and reasoning effort](../research/copilot-cli-models-and-reasoning-effort.md).

See [Agent Client](../concepts/str-agent-client.md) for how agent invocation is applied.
