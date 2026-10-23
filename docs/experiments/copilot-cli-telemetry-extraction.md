# Experiment: extracting loop telemetry fields from Copilot CLI

Builds on [copilot-cli-json-output-events.md](copilot-cli-json-output-events.md). Goal: replace each fake in the [Aspire prototype](../prototypes/dashboard-aspire/README.md) (`FakeAgent`) with data the real CLI produces. Run 2026-10-23.

## Question

Which Copilot CLI output carries each attribute the prototype's `invoke_agent` / `execute_tool` spans and metrics need (model, usage, cost, context, messages, tools)?

## Method

- Read the prototype's span contract and [fake_agent.py](../prototypes/dashboard-aspire/fakeloop/fake_agent.py).
- Re-read the captured headless run ([cop-resp](../prototypes/cop-resp/output/stdout.jsonl), CLI 1.0.92: stdout JSONL plus its `~/.copilot/session-state/<id>/events.jsonl`) and a 1.0.100 session log.
- Read `copilot help monitoring` (CLI 1.0.103) and the [VS Code OTel attribute reference](https://code.visualstudio.com/docs/agents/guides/monitoring-agents).
- Wrote [copilot_session.py](../prototypes/dashboard-aspire/fakeloop/copilot_session.py), which builds the fake's fields from a real `events.jsonl`, and ran it on both logs.
- A live `copilot -p` run was **not possible**: the Copilot API was unreachable from this environment (`ECONNRESET` on `/models`). Everything below is from existing captures, the on-disk logs and the docs.

## Observations

### Three sources, different completeness

| Source | Available | Usage detail |
| --- | --- | --- |
| stdout `--output-format json` | live, per run | model, tool calls, messages; only `totalPremiumRequests` + `totalNanoAiu` (`session.usage_checkpoint`) and `result.usage` (`premiumRequests`, `totalApiDurationMs`, `sessionDurationMs`, `codeChanges`). No token counts. |
| `~/.copilot/session-state/<sessionId>/events.jsonl` | after/while the run, every CLI version | `session.shutdown`: per-model `usage` (input/output/cacheRead/cacheWrite/reasoning tokens), `tokenDetails`, `totalNanoAiu`, `currentTokens`, `systemTokens`, `conversationTokens`, `toolDefinitionsTokens`. From CLI 1.0.95: one `session.usage_record` per model call (see below). |
| native OpenTelemetry (`copilot help monitoring`) | opt-in env vars | `invoke_agent` → `chat <model>` → `execute_tool <tool>` spans with `gen_ai.*` / `github.copilot.*`, plus token metrics. |

- The session id is the `result.sessionId` stdout line, or pin it with `--session-id <uuid>`. loop resumes by `--name`/`--resume`; a resumed session appends to the same `events.jsonl`.
- `session.usage_record` (CLI ≥ 1.0.95, not in 1.0.92) carries `model`, `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`, `reasoningTokens`, `cost`, `duration`, `timeToFirstTokenMs`, `finishReason`, `reasoningEffort`, `maxPromptTokens`, `numToolCalls`, `initiator`, `apiCallId`, and `copilotUsage.tokenDetails[]` (`tokenType`, `tokenCount`, `costPerBatch`, `batchSize`) with `totalNanoAiu`. `apiCallId` equals `assistant.message.data.apiCallId`, so usage joins to the message and its tool requests.
- `inputTokens` **includes** cache reads and writes (32219 = 4 + 0 + 32215 in one record; 63952 = 4 + 31836 + 32112 in the 1.0.92 shutdown). The uncached input is `tokenDetails.input`, or `inputTokens - cacheRead - cacheWrite`.
- `tokenDetails` costs sum exactly to `totalNanoAiu` (4 input + 32215 cache-write + 393 output tokens = 8 447 550 000 nano-AIU). The unit is **AIU (1e9 nano), not USD**.
- Per-call `cost` is `1.0` on every call, agent-initiated ones included (52 records, 52 × 1.0); the billed premium requests is 1. Count `initiator == "user"` records, or read `session.shutdown.totalPremiumRequests` / `result.usage.premiumRequests`.
- `tool.execution_start` and `tool.execution_complete` share `toolCallId`; there is no duration field, so the span duration is the timestamp difference (51 ms for the shell call in the captured run). `success`, `result.content` and `shellExecution.exitCode` are on the complete event.
- Native OTel: content (prompts, system instructions, tool arguments/results) is captured only with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. `COPILOT_OTEL_FILE_EXPORTER_PATH` writes all signals as JSONL (offline route). The help text says an `otlp-http` exporter whose endpoint resolves to `http://` has export disabled; its examples use `http://localhost:4318`, so this is unresolved.

### Field mapping

| Prototype attribute | Real source |
| --- | --- |
| `gen_ai.conversation.id` | `result.sessionId` (stdout), `session.start.sessionId` |
| `gen_ai.request.model` | `--model` we pass; `session.start.selectedModel`, `session.model_change.newModel` |
| `gen_ai.response.model` | `assistant.message.model`, `usage_record.model` |
| `gen_ai.provider.name`, `loop.agent.coding_agent` | constant `copilot`; `usage_record.provider.id` |
| `loop.agent.thinking` | `--reasoning-effort` we pass; `session.start.reasoningEffort`, `usage_record.reasoningEffort` |
| `gen_ai.system_instructions` | `system.message.content` (events.jsonl only; OTel with content capture) |
| `gen_ai.input.messages` | `user.message.content` (`transformedContent` adds the datetime header) |
| `gen_ai.output.messages` | last `assistant.message.content`; reasoning in `reasoningText` |
| `gen_ai.response.finish_reasons` | `usage_record.finishReason` (`stop`, `tool_calls`) |
| `gen_ai.usage.input_tokens` / `loop.usage.input_tokens` | `tokenDetails.input` (uncached) for the loop breakdown; raw `inputTokens` for the semconv total |
| `…output_tokens`, `…reasoning_tokens`, `…cache_read_tokens`, `…cache_write_tokens` | `usage_record` or `session.shutdown.modelMetrics.*.usage` |
| `loop.usage.{type}_cost`, `loop.usage.total_cost` | `copilotUsage.tokenDetails` × `costPerBatch / batchSize / 1e9` (AIU); total = `totalNanoAiu / 1e9`. No per-type split in 1.0.92 shutdown |
| `loop.agent.context_window` | `usage_record.maxPromptTokens` |
| `loop.agent.context_tokens` | `session.shutdown.currentTokens` (+ `systemTokens`, `conversationTokens`, `toolDefinitionsTokens`) |
| `gen_ai.client.operation.duration` | stdout `result.usage.totalApiDurationMs` or `sessionDurationMs`; per call `usage_record.duration` |
| `execute_tool` name, call id, arguments | `tool.execution_start.toolName`, `toolCallId`, `arguments` |
| `execute_tool` result, `loop.tool.ok` | `tool.execution_complete.result.content`, `success` |
| `execute_tool` duration | `tool.execution_complete.timestamp − tool.execution_start.timestamp` |
| phase, ticket, gate, attempt, retries, envelope | loop's own state; not in the CLI |

Running [copilot_session.py](../prototypes/dashboard-aspire/fakeloop/copilot_session.py) on both logs filled every row above that the log carries. The 1.0.92 log gave the shutdown-summary values (4 input, 339 output, 31836 cache-read, 32112 cache-write, 9.0045 AIU, 1 premium request, 1 tool call of 0.05 s). The 1.0.100 log gave per-call values including the per-type AIU split, `maxPromptTokens` 872000 and reasoning effort `high`. `context_tokens` was empty there because that session had no `session.shutdown` yet.

## Conclusion

- **Primary route: read the session log after each agent turn.** Take the id from `result.sessionId` (or pin it with `--session-id`), read `events.jsonl`, and build the `invoke_agent` span and its `execute_tool` children from it, as [copilot_session.py](../prototypes/dashboard-aspire/fakeloop/copilot_session.py) does. This is the only verified path to token counts, and it needs no change to how loop spawns the CLI. For a resumed session, take the records after the turn's own start so earlier turns are not counted.
- **Live visibility during a turn:** stdout events (`tool.execution_start/complete`, `assistant.message`, `session.usage_checkpoint`) give tools and premium requests as they happen; token counts only arrive from the log.
- **Alternative: native OTel.** Setting `OTEL_EXPORTER_OTLP_ENDPOINT` (plus content capture) on the spawned process would put `gen_ai.*` spans in Aspire without parsing, but loop's `loop.*` phase/gate/role attributes and resource coloring would still need loop's own spans, and nesting the CLI's spans under loop's agent span needs trace-context propagation that is not documented in `copilot help monitoring`. Treat as unverified.
- **Cost:** `loop.agent.cost` is declared in USD in the prototype but the CLI reports AIU and premium requests. Rename the metric (`loop.agent.aiu`, `loop.agent.premium_requests`) or add an explicit, configurable rate; do not reuse the prototype's per-million-token dollar table.
- **Version floor:** per-call usage needs CLI ≥ 1.0.95; older CLIs only have the shutdown summary (no per-type AIU, no `maxPromptTokens`).

## Not verified

- A live headless run on CLI ≥ 1.0.95: whether its `--output-format json` stdout also streams `session.usage_record`, and whether the shutdown summary is cumulative or per-turn across `--resume`.
- The AIU → USD rate.
- Whether the CLI honours an incoming trace context, and whether `http://localhost:4318` exports work from the CLI's `otlp-http` exporter.
- The OTel file-exporter output shape (`COPILOT_OTEL_FILE_EXPORTER_PATH`).

## Follow-up 2026-10-23

The prototype now has a real mode that implements the conclusion above (`run_fake.py` without `--dry`; see its README). The API became reachable later the same day and one full run completed against Aspire: 67 spans in 2 min 46 s, with real `invoke_agent` spans and their `execute_tool` children for the planner, developer and reviewer.

Confirmed by that run on CLI 1.0.103 with `claude-haiku-4.5`:
- `--session-id <uuid>` makes the CLI write `~/.copilot/session-state/<uuid>/events.jsonl`, so the log can be found by an id the caller chose.
- The scoped `--allow-tool`/`--deny-tool` set (`write`, `shell(git|python|python3|pytest:*)`, deny `git push`) was enough for the agents to finish.
- Tool spans built from the log's start/complete timestamps have real, millisecond-scale durations.

Not checked in Aspire yet: the usage and AIU attributes and the metrics (only the trace tree was viewed), stdout streaming of `session.usage_record`, and resumed-session summaries.

One prompt problem surfaced: passing the whole plan as the ticket body made the first developer agent implement both tickets, so a later commit step found nothing to commit. Real tickets now carry their own body.
