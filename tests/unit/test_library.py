from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src" / "loop"
WORKFLOWS = ROOT / "workflows"


def test_packaging_declares_no_scripts_and_ships_no_workflow_file() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    workflow_files = {p.name for p in WORKFLOWS.glob("*.py") if p.name != "__init__.py"}
    package_files = {p.name for p in SRC.rglob("*.py") if p.name != "__init__.py"}

    assert "scripts" not in config["project"]
    assert not workflow_files & package_files


def test_import_linter_contracts_pass() -> None:
    lint_imports = Path(sys.executable).parent / "lint-imports"
    result = subprocess.run([str(lint_imports)], cwd=ROOT, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
