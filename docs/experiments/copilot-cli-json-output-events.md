# Experiment: Copilot CLI `--output-format json` event shape

Builds on [copilot-cli-p-mode-permissions.md](copilot-cli-p-mode-permissions.md). Run 2026-10-06.

## Question

Which flag makes `copilot -p` emit JSON events in non-interactive mode, and what is the shape of the streamed events — in particular `assistant.message_delta` and the end-of-turn event? Needed to build `CopilotClient`'s JSON parser and the `FakeCopilotCli` test double from an observed, not assumed, shape.

## Method

- **CLI under test:** the `copilot` on `PATH` in this environment, version 1.0.92 (binary at `~/.vscode-server/data/User/globalStorage/github.copilot-chat/copilotCli/copilot`, not the research-note shim).
- **Command:** `copilot -p "Reply with the single word OK" --output-format json --allow-all-tools --no-color`, run in a scratch directory (`/tmp/loop-copilot-json-exp`), stdout and stderr captured to files. No other tools, paths, or URLs granted. Nothing in the repo was touched.

## Observations

- `--output-format json` produces JSONL: one JSON object per line on stdout. Exit code was 0.
- Every event has `type`, `id`, `timestamp`, `parentId`; most carry `ephemeral: true` except the ones below.
- Confirmed event sequence for a plain text reply (no tool calls): `session.mcp_server_status_changed` (repeated, MCP startup) → `session.extensions_loaded` → `session.tools_updated` → `session.mcp_servers_loaded` → `user.message` → `assistant.turn_start` → `model.call_start` → `assistant.message_start` → **`assistant.message_delta`** (one or more) → `model.call_finished` → **`assistant.message`** (the full, non-ephemeral message) → `model.call_final_result` → **`assistant.turn_end`** → `session.usage_checkpoint` → `assistant.idle` → **`result`** (final line, no `type` nesting under `data`: `{"type":"result","sessionId":...,"exitCode":0,"usage":{...}}`).
- `assistant.message_delta` shape: `{"type":"assistant.message_delta","data":{"messageId":"<uuid>","deltaContent":"OK"},"ephemeral":true,...}`. The assistant's visible text is the concatenation of `data.deltaContent` across all `assistant.message_delta` events in a turn, in stream order.
- `assistant.message` (sent once per turn, not ephemeral) repeats the full accumulated text in `data.content`, which matched the delta concatenation exactly in this run (`"OK"`).
- `assistant.turn_end` carries only `{"turnId":...}`; it is the per-turn end-of-turn signal. `result` is the process-level final line and also carries `exitCode`.
- Non-JSON lines did not occur on stdout in this run; everything was one JSON object per line as documented.

## Conclusion

- Use `--output-format json` (not `--silent`, which is for text mode).
- Parse `assistant.message_delta` events and concatenate `data.deltaContent` for the assistant's running text; this is the stream `CopilotClient` watches for the completion signal, checking the accumulated text after each delta rather than waiting for `assistant.message`/`assistant.turn_end`, since the signal must be caught live.
- Treat a line that fails to parse as JSON, or whose `type` is not `assistant.message_delta`, as a no-op for text accumulation; every raw line is still logged for diagnostics regardless of parse outcome.
- `FakeCopilotCli` emits event lines (default: wraps a plain string into one synthetic `assistant.message_delta` event, so simple tests stay simple; also accepts a literal list of pre-built event-line strings for scripting multi-event and completion-signal scenarios).
