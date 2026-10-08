"""Settings as code; --harness-root, --log-dir, and --log-level are the only CLI overrides."""

from __future__ import annotations

import threading
from pathlib import Path

from loop import Hook, NoSandbox, Sandbox
from workflows.platforms.work_tracking import RepositoryConfig

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
HOOKS: tuple[Hook, ...] = ()
# Single repo: this checkout is both the harness and the only target repository.
REPOSITORIES: tuple[RepositoryConfig, ...] = (
    RepositoryConfig(path=Path(__file__).resolve().parents[2], owner_repo="antshc/loop", is_harness=True),
)
MAX_TICKET_FAILURES = 2
PROMPT = Path(__file__).parent / "prompts" / "dev.md"


def _no_sandbox(workspace: Path, cancel: threading.Event) -> Sandbox:
    return NoSandbox(workspace, cancel=cancel)


SANDBOX_FACTORY = _no_sandbox
