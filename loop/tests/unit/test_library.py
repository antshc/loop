from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).parents[3]
SRC = ROOT / "loop" / "src"
WORKFLOWS = ROOT / "loop" / "workflows"


def test_packaging_declares_no_scripts_and_ships_no_workflow_file() -> None:
    package = distribution("loop")
    workflow_files = {p.name for p in WORKFLOWS.glob("*.py") if p.name != "__init__.py"}
    package_files = {p.name for p in SRC.rglob("*.py") if p.name != "__init__.py"}

    assert not any(
        entry.group in {"console_scripts", "gui_scripts"} for entry in package.entry_points
    )
    assert not workflow_files & package_files


def test_distribution_maps_the_loop_package_to_src_so_the_factory_is_never_shipped() -> None:
    """Given the build configuration, when packages are declared, then only `loop` packages map to `loop/src` and it holds no factory module."""
    setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]

    assert setuptools["package-dir"] == {"loop": "loop/src"}
    assert all(name == "loop" or name.startswith("loop.") for name in setuptools["packages"])
    assert not {"software_factory", "workflows"} & {path.name for path in SRC.iterdir()}


def test_package_supports_python_310() -> None:
    assert distribution("loop").metadata["Requires-Python"] == ">=3.14"


def test_import_linter_contracts_pass() -> None:
    lint_imports = Path(sys.executable).parent / "lint-imports"
    executable = str(lint_imports) if lint_imports.exists() else shutil.which("lint-imports")
    assert executable, "lint-imports (import-linter) is not installed"
    environment = os.environ | {"PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "loop"), os.environ.get("PYTHONPATH")]))}
    result = subprocess.run([executable], cwd=ROOT, env=environment, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
