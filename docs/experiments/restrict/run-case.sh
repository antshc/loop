#!/usr/bin/env bash
# Disposable experiment driver for issue #22. Usage: run-case.sh <id> <cwd-relative-to-this-dir> <prompt> [copilot flags...]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
COPILOT="${COPILOT:-/tmp/exp22-cli/node_modules/.bin/copilot}"
SHARED="${EXP22_NUGET:-$HOME/exp22-shared/nuget}"
OUT="${EXP22_OUT:-/tmp/exp22-out}"
id="$1"; cwd="$2"; prompt="$3"; shift 3
mkdir -p "$OUT"
find "$HERE/harness/workspace/repo1" \( -name bin -o -name obj \) -type d -prune -exec rm -rf {} +
[ "${KEEP_NUGET:-0}" = 1 ] || { rm -rf "$SHARED"; mkdir -p "$SHARED"; }
cd "$HERE/$cwd" || exit 2
env DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1 NUGET_PACKAGES="$SHARED" COPILOT_AUTO_UPDATE=false \
  "$COPILOT" -p "$prompt" --model gpt-5-mini --effort low --no-auto-update --no-ask-user \
  --output-format json "$@" >"$OUT/$id.jsonl" 2>"$OUT/$id.err"
echo "exit=$?" >"$OUT/$id.exit"
python3 - "$OUT/$id.jsonl" <<'PY'
import json, sys
calls = {}
for line in open(sys.argv[1]):
    try: e = json.loads(line)
    except ValueError: continue
    t, d = e.get("type", ""), e.get("data", {})
    if t == "tool.execution_start":
        a = d.get("arguments") or {}
        calls[d["toolCallId"]] = "%s %s" % (d.get("toolName"), (a.get("command") or a.get("path") or a.get("url") or json.dumps(a))[:70])
    elif t == "tool.execution_complete":
        print("%s -> %s" % (calls.get(d["toolCallId"], "?"), "ok" if d.get("success") else (d.get("error") or {}).get("code", "fail")))
PY
cat "$OUT/$id.exit"
echo "result_in_output=$(grep -c 'EXP22-RESULT: hello xthree' "$OUT/$id.jsonl")"
echo "nuget=$(ls "$SHARED" 2>/dev/null | tr '\n' ' ')"
echo "bin_obj=$(find "$HERE/harness/workspace/repo1" \( -name bin -o -name obj \) -type d | wc -l)"
