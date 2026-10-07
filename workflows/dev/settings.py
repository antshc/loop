"""Settings as code; --harness-root, --log-dir, and --log-level are the only CLI overrides."""

from __future__ import annotations

import threading
from pathlib import Path

from loop import Hook, NoSandbox, Sandbox

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
HOOKS: tuple[Hook, ...] = ()
MAX_TICKET_FAILURES = 2
PROMPT = Path(__file__).parents[1] / "prompts" / "dev.md"
HITL_LABEL = "hitl"


def _no_sandbox(workspace: Path, cancel: threading.Event) -> Sandbox:
    return NoSandbox(workspace, cancel=cancel)


SANDBOX_FACTORY = _no_sandbox
