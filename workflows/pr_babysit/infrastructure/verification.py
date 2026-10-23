from __future__ import annotations

import subprocess
from pathlib import Path


class Verification:
    """Run workflow-configured build and test commands before publication."""

    def __init__(self, *, run=subprocess.run) -> None:
        self._run = run

    def __call__(self, path: Path, repository, timeout: float) -> None:
        """Execute both gates without shell interpolation."""
        for command in (repository.build, repository.test):
            self._run(list(command), cwd=path, check=True, capture_output=True, text=True, timeout=timeout)
