# Stream agent output live through the Sandbox and parse it in the provider adapter

The agent's completion output result must be caught as the agent emits it, and each agent CLI reports its output in its own event format (Copilot CLI emits `assistant.message_delta` events; other agents use different tags). The Sandbox executor streams the agent process's output line by line to the agent client while the process runs, and each provider adapter (e.g. `CopilotClient`) parses its own event format into the provider-neutral result Loop acts on. When the parsed output carries the completion signal, the adapter marks the result completed and terminates the agent process, so a session that idles or hangs after declaring itself done costs no time until the timeout.

## Considered Options

- **Run the agent with `--silent` and parse captured stdout after the process exits** — rejected: the completion output result has to be caught live, not reconstructed after exit.
- **Parse `assistant.message_delta` events in shared Loop code or a shared result model** — rejected: the event type is Copilot-specific; other agents use different tags.
- **Record the completion signal but let the process exit on its own** — rejected: a session that keeps running after declaring completion burns time until the timeout.
- **Ignore the signal; stream only for live logging and decide completion from the report parsed after exit** — rejected: the completion output result has to be caught live.
- **Keep the POC's standalone `AIAgent` streaming wrapper** — rejected: every agent invocation runs through a Sandbox, and provider CLI handling stays inside the provider adapter.

## Consequences

- The `Sandbox` contract and every Sandbox implementation (`NoSandbox`, `DockerSandbox`) gain a streaming execution path; this widens the compatibility surface set by [ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md).
- Adding an agent provider means writing its event parser; nothing outside its adapter changes.
- A signal-triggered stop must be distinguishable from a timeout: a completed result is a success even though the terminated process exits non-zero.
- A workflow's machine-readable report must precede the completion signal in the agent output, or be carried in it, since output after the signal is lost.

See [Agent Client](../concepts/str-agent-client.md) for how agent invocation and output capture are applied.
