"""Architecture: the import-linter contracts in pyproject.toml hold for the library, the factory and the examples."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# import-linter reconfigures logging, so it runs in its own interpreter to leave the test session's loggers untouched.
LINT = "import sys; from importlinter.cli import lint_imports; sys.exit(lint_imports(no_cache=True, no_logo=True))"


def test_every_import_contract_is_kept() -> None:
    """Given the repository's import-linter contracts, when they are checked, then none is broken."""
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}

    result = subprocess.run([sys.executable, "-c", LINT], cwd=REPO_ROOT, env=environment, capture_output=True, text=True, timeout=300)

    assert result.returncode == 0, result.stdout + result.stderr
