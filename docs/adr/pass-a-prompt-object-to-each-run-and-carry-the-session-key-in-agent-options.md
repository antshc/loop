# Pass a Prompt object to each run and carry the session key in agent options

Runs passed the template and its arguments as two parameters and the session id as a third, so a Workflow that already holds its final text still had it re-rendered, and a `{{X}}` or ``!`cmd` `` arriving in that text raised an error or ran on the host. So `AgentRunner.run` and `AgentClient.run` take one `Prompt` (template plus optional `args`) before the optional `model`, `reasoning_effort`, and `options`; a `Prompt` with no `args` is sent verbatim, with no placeholder substitution or command expansion; and the logical session key lives only in `AgentOptions.session_key`, which the runner reads, with `new_session=True` still creating one and `AgentRunResult` still returning it.

## Considered Options

- **Keep `run(prompt: str, prompt_args, ...)`** — rejected: the template and its arguments are one value, and passing them apart invites rendering text that is already final.
- **Accept both `str` and `Prompt` on `run`** — rejected: two ways to pass one value, which [the model decision](pass-the-model-and-reasoning-effort-as-run-arguments.md) also rejected.
- **Always preprocess, even without `args`** — rejected: text the Workflow rendered itself (a task, an agent-written plan) would raise on `{{X}}` and run ``!`cmd` `` on the host.
- **A `literal=True` flag on `Prompt`** — rejected: an extra switch where "no args" already means "already rendered".
- **Keep `session_id` as an `AgentRunner.run` parameter** — rejected: the session key already belongs to `AgentOptions`; one name and one place.

## Consequences

- Breaks user-supplied workflows that call `run` with a string, `prompt_args`, or `session_id=` ([Ship Loop as a workflow library](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)); replaces the `run` signature set by [Run agents through an agent runner](run-agents-on-the-host-through-an-agent-runner-that-binds-one-agent-client.md) and [Pass the model and reasoning effort as run arguments](pass-the-model-and-reasoning-effort-as-run-arguments.md).
- A template that relies on ``!`cmd` `` expansion must be passed with `args` (an empty mapping is enough).
- `AgentRunResult.session_id` becomes `session_key`.

See [Agent Client](../concepts/str-agent-client.md) for how agent invocation is applied.
