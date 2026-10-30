"""Agent CLI hook shim: normalises a CLI's native hook payload for a user command.

Usage: python shim.py --cli <name> --point <point> --session <SessionName> -- <command>
Observe-only: it discards the command's stdout and always exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any


def _first(raw: Mapping[str, Any], *keys: str) -> Any:
    return next((raw[key] for key in keys if key in raw), None)


def normalise(cli: str, point: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Map Copilot (camelCase) and Codex (snake_case) payloads to one shape."""
    source = raw.get("source")
    return {
        "point": point,
        "cli": cli,
        "handle": _first(raw, "sessionId", "session_id"),
        "cwd": raw.get("cwd"),
        "resumed": source == "resume",
        "reason": raw.get("reason"),
        "tool": _first(raw, "toolName", "tool_name"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--point", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    try:
        args = parser.parse_args(argv)
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        try:
            raw = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            raw = {}
        payload = normalise(args.cli, args.point, raw if isinstance(raw, dict) else {})
        payload["session"] = args.session
        env = {**os.environ, "LOOP_SESSION": args.session, "LOOP_CLI": args.cli, "LOOP_HOOK_POINT": args.point}
        result = subprocess.run(
            " ".join(command), shell=True, input=json.dumps(payload) + "\n", env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            print(f"agent-cli-hook: {args.point} command exited {result.returncode}", file=sys.stderr)
    except BaseException as error:  # fail open: a hook must never break the CLI run
        print(f"agent-cli-hook: {error!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
