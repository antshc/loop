"""Thin wrapper around the Copilot CLI for code-review operations."""

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

def _is_dry_run() -> bool:
    """Return True (dry-run on) unless AFK_DRY_RUN is explicitly '0' or 'false'."""
    val = os.environ.get("AFK_DRY_RUN", "1")
    return val.lower() not in ("0", "false")


class AIAgent:
    """Copilot CLI agent for automated code-review operations."""

    def __init__(self, *, alias: str = "yolo", prompt: str = "/ralph:fix") -> None:
        self._alias = alias
        self._prompt = prompt
        logger.info("AIAgent initialized", extra={"alias": alias, "prompt": prompt})

    def run(self) -> None:
        """Run the Copilot agent on the given review threads."""
        logger.info("run() called")
        proc = self._run(self._prompt)
        if proc is not None:
            self._stream_text(proc)
        logger.info("run() finished")

    def _run(self, prompt: str) -> subprocess.Popen | None:
        cmd = [self._alias, "-p", prompt]

        if _is_dry_run():
            logger.info("dry-run: skipping copilot agent", extra={"command": cmd})
            return None

        logger.debug("spawning process", extra={"cmd": cmd})
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _stream_text(self, proc: subprocess.Popen) -> str:
        logger.debug("streaming output", extra={"pid": proc.pid})
        full_text = ""
        for line in proc.stdout:
            line = line.strip()
            logger.info(line)
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("non-JSON line skipped", extra={"line": line})
                continue
            if event.get("type") == "assistant.message_delta":
                delta = event.get("data", {}).get("deltaContent", "")
                if delta:
                    full_text += delta

        proc.wait()
        logger.debug("process exited", extra={"returncode": proc.returncode})
        logger.info("agent response", extra={"text": full_text})
        return full_text
