# Copilot CLI native OpenTelemetry in Aspire

A regular `copilot` session, interactive or `-p`, exports its own OpenTelemetry traces and metrics (`copilot help monitoring`) to a standalone [Aspire dashboard](https://aspire.dev/dashboard/standalone/). No parsing of `events.jsonl` and no custom code: the CLI is the only producer, Aspire is the only viewer. This is the "native OTel" route from [the telemetry extraction experiment](../../experiments/copilot-cli-telemetry-extraction.md).

```
copilot-otel.sh ──OTLP/HTTP :4328──▶ Aspire dashboard (UI :18898)
```

It has its own Aspire instance on its own host ports (UI `18898`, OTLP gRPC `4327`, OTLP HTTP `4328`), so it runs next to [dashboard-aspire](../dashboard-aspire/README.md).

## Run

```
cd docs/prototypes/copilot-otel-aspire
docker compose up -d                  # Aspire 13.2
./copilot-otel.sh                     # interactive session; any copilot arguments pass through
./copilot-otel.sh -p "your prompt"    # or a headless run
```

Open <http://localhost:18898>: **Traces** shows one `github-copilot: invoke_agent` trace per prompt, with the `chat <model>` and `execute_tool <tool>` spans below it. The trace row has a **GenAI details** button. **Metrics** shows the token and duration metrics.

## Without the script

Set the variables on any `copilot` process:

```
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4328
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
copilot
```

`OTEL_EXPORTER_OTLP_ENDPOINT` alone enables export. Leave `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` unset to keep prompts, system instructions and tool arguments/results out of the spans. `COPILOT_OTEL_FILE_EXPORTER_PATH=<file>` writes the same signals as JSON lines instead.

## Observed

Run 2026-10-23, CLI 1.0.103, `copilot -p "Reply with the single word: pong"`:

- The export to an `http://localhost` OTLP endpoint works. The help text's warning about `http://` endpoints applies to the dynamic-headers helper, not to plain export.
- Aspire received a `github-copilot: invoke_agent` trace with 2 spans (1.56 s).
- Only the trace list was checked; the span attributes and metrics pages were not inspected.

## Stop

```
docker compose down
```
