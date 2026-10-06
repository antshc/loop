# Stream agent output live and parse it after exit with a per-agent-kind output parser

Each agent CLI reports its output in its own event format (Copilot CLI emits `assistant.message_delta` events; Codex and others differ), and workflows must act on the agent's response rather than on raw provider events. The Sandbox executor streams the agent process's output line by line to the agent client for live logging while the process runs; once the process exits, the agent client hands the captured output to an `AgentOutputParser` post-processor. The `loop` library defines that abstraction and one implementation per agent kind — only Copilot is supported — and the parser both extracts the agent's response into a string field of `AgentResult` (JSON for a JSON parser, other text for other implementations) and decides whether the run completed successfully or with an error. The parser knows only a generic response envelope — a JSON object with `identifier`, `status` (`completed` or `failed`), and a `result` object — and judges success from `status`; the workflow owns the `result` shape and checks the `identifier` against its Ticket (`<initiative-id>|<ticket-number>` for `dev`). There are no framing tags, no completion sentinel, and no early termination: the agent process runs until it exits.

## Considered Options

- **Make the parser task-aware: pass the expected task id in the run options and reject mismatches and missing fields in the parser** — rejected: Ticket-specific checks belong with the workflow, which already owns the Git checks; the parser stays reusable across workflows.

- **Detect a completion sentinel (`<promise>COMPLETE</promise>`) in the live stream and terminate the agent on it** — rejected: the `identifier` in the response identifies it as the current Ticket's response and distinguishes it from any other JSON the agent emits, so a sentinel is a second contract to keep in step.
- **Terminate the agent process as soon as the response object appears in the stream** — rejected: parsing runs once as a post-processor over the finished output, not inside the live line callback.
- **Wrap the response in `<response>…</response>` tags** — rejected: Copilot's JSON event stream can be parsed directly; tags can be added later without changing the JSON body.
- **Keep the report parser (`parse_report`) in the workflow** — rejected: extracting the response depends on the agent kind's output format, not on the workflow, and must be replaceable per agent kind.
- **Collect text and detect completion inside the adapter's live line callback** — rejected: it ties response extraction to one provider's client and cannot be replaced per agent kind.
- **Parse `assistant.message_delta` events in shared Loop code** — rejected: the event type is Copilot-specific; other agents use different tags.
- **Keep the POC's standalone `AIAgent` streaming wrapper** — rejected: every agent invocation runs through a Sandbox, and provider CLI handling stays inside the provider adapter.

## Consequences

- `AgentOutputParser`, its per-agent-kind implementations, and the `AgentResult` response field are part of the library's compatibility surface set by [ADR 0003](0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md).
- Adding an agent kind means writing its output parser; nothing outside its adapter changes.
- An agent that has already responded but keeps running costs time until it exits or times out.

See [Agent Client](../concepts/str-agent-client.md) for how agent invocation and output capture are applied.
