"""DISPOSABLE experiment: can the agent's final JSON result be parsed from `copilot --output-format=json`?

Runs copilot in a scratch git repo, saves the raw JSONL to output/, and parses the result.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / "output"

PROMPT = """\
In the current git repository, create a file hello.txt containing "hello" and commit it
with the message "ccode: add hello". Then return the result as a single JSON object and nothing else.

Success:
{"status":"completed","commit":"<sha>","summary":"<summary>","verification":"<verification>"}

Failure:
{"status":"failed","reason":"<reason>"}
"""


def run_copilot(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["copilot", "-p", PROMPT, "--no-ask-user", "--allow-all-tools", "--no-color", "--output-format=json"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def parse_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"non-JSON line: {line!r}", file=sys.stderr)
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def final_text(events: list[dict]) -> str:
    """Last non-ephemeral `assistant.message` content; falls back to concatenated deltas."""
    messages = [e["data"]["content"] for e in events if e.get("type") == "assistant.message"]
    if messages:
        return messages[-1]
    return "".join(e["data"]["deltaContent"] for e in events if e.get("type") == "assistant.message_delta")


def parse_result(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"no JSON object in agent text: {text!r}")
    return json.loads(text[start : end + 1])


def main() -> int:
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cop-resp-") as scratch:
        cwd = Path(scratch)
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.email", "exp@example.com"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.name", "exp"], cwd=cwd, check=True)
        process = run_copilot(cwd)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=False)

    (OUTPUT / "stdout.jsonl").write_text(process.stdout)
    (OUTPUT / "stderr.txt").write_text(process.stderr)
    print(f"exit={process.returncode} real HEAD={head.stdout.strip() or '(none)'}")

    events = parse_events(process.stdout)
    types = [e.get("type") for e in events]
    print(f"{len(events)} events; last type={types[-1] if types else None}")
    print("result event:", next((e for e in reversed(events) if e.get("type") == "result"), None))

    text = final_text(events)
    print(f"final text: {text!r}")
    result = parse_result(text)
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print("parsed:", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
