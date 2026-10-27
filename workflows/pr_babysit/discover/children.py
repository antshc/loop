"""Independent maintenance processes, one per PR."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol


class Child(Protocol):
    """The subset of `subprocess.Popen` the scheduler supervises."""

    returncode: int | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class ChildLauncher(Protocol):
    """Starts the maintenance process of one PR."""

    def start(self, config: Path, url: str) -> Child: ...


def child_command(config: Path, url: str) -> list[str]:
    """An independent babysit process without a scheduler trace parent."""
    return [sys.executable, "-m", "workflows.pr_babysit", "--config", str(config), "--pr", url]


def child_environment() -> dict[str, str]:
    """Keeps exporter configuration but discards inherited distributed trace context."""
    return {key: value for key, value in os.environ.items()
            if key.lower() not in {"traceparent", "tracestate", "baggage"}}


class ProcessLauncher:
    """Starts `python -m workflows.pr_babysit --pr <url>` as a child process."""

    def start(self, config: Path, url: str) -> Child:
        return subprocess.Popen(child_command(config, url), env=child_environment())
