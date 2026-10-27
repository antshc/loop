#!/usr/bin/env bash
# One-command repro: bun run + bun build --compile, plus a dead-endpoint timeout probe. Needs bun on PATH or ~/.bun/bin.
set -u
cd "$(dirname "$0")"
export PATH="$HOME/.bun/bin:$PATH"
export PORT="${PORT:-4318}" OTLP_ENDPOINT="http://127.0.0.1:${PORT:-4318}"
mkdir -p out
bun --version
bun install --frozen-lockfile >/dev/null 2>&1 || bun install >/dev/null

bun receiver.ts > out/receiver.log 2>&1 &
RECV=$!
trap 'kill $RECV 2>/dev/null' EXIT
for _ in $(seq 50); do curl -s -o /dev/null -X POST "$OTLP_ENDPOINT/ping" && break; sleep 0.1; done
status=0

echo "== bun run =="
: > out/received.jsonl
bun spike.ts && bun verify.ts bun-run || status=1

echo "== bun build --compile =="
bun build --compile spike.ts --outfile out/spike-bin >/dev/null && ls -lh out/spike-bin | awk '{print "binary size:", $5}'
: > out/received.jsonl
./out/spike-bin && bun verify.ts compiled || status=1

probe() { # label endpoint
  echo "== $1 (export timeout 2s; expect clean exit, no hang) =="
  local s; s=$(date +%s.%N)
  OTLP_ENDPOINT=$2 EXPORT_TIMEOUT_MS=2000 timeout 60 bun spike.ts 2>&1 | grep -E "FAILED|ok in|shutdown complete"
  echo "exit=${PIPESTATUS[0]} elapsed=$(echo "$(date +%s.%N) - $s" | bc)s"
}
probe "connection refused" http://127.0.0.1:1
HANG=1 PORT=4319 bun receiver.ts > out/receiver-hang.log 2>&1 &
HANGPID=$!
trap 'kill $RECV $HANGPID 2>/dev/null' EXIT
sleep 1
probe "accepts but never responds" http://127.0.0.1:4319

rm -f out/spike-bin
exit $status
