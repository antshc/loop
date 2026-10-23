#!/usr/bin/env bash
# Run `copilot` with its native OpenTelemetry export pointed at this prototype's Aspire dashboard.
# Every argument is passed through, so `./copilot-otel.sh`, `./copilot-otel.sh -p "..."` and `--resume` all work.
set -euo pipefail

export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4328}"
export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}"
export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-github-copilot}"
# Content capture puts prompts, system instructions and tool arguments/results in the spans.
export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="${OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT:-true}"

exec copilot "$@"
