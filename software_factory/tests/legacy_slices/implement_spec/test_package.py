from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from software_factory.legacy_slices.implement_spec import HARNESS
from software_factory.tests.legacy_slices.implement_spec.doubles import ImplementSpecHarness

ROOT = Path(__file__).resolve().parents[4]


def test_harness_paths_survive_the_package_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert HARNESS.checkout == ROOT
    assert HARNESS.worktrees == ROOT / "workspace" / f"{ROOT.name}.worktrees"


def test_implement_spec_runs_with_the_role_agents_and_the_shared_package_but_no_sibling_workflow(tmp_path: Path) -> None:
    factory = tmp_path / "software_factory"
    package = factory / "legacy_slices" / "implement_spec"
    shutil.copytree(ROOT / "software_factory/legacy_slices/implement_spec", package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "software_factory/shared", factory / "shared", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "software_factory/agents", factory / "agents", ignore=shutil.ignore_patterns("__pycache__"))
    (factory / "__init__.py").write_text("")
    (package.parent / "__init__.py").write_text("")
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}

    help_result = subprocess.run(
        [sys.executable, "-m", "software_factory.legacy_slices.implement_spec", "--help"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "--log-dir" in help_result.stdout and "--log-level" in help_result.stdout

    resources = subprocess.run(
        [
            sys.executable, "-c",
            "from importlib import resources; from pathlib import Path; "
            "from software_factory.legacy_slices.implement_spec import HARNESS; "
            "from software_factory.agents.roles.developer import DEVELOPER; "
            "prompt = resources.files('software_factory.agents.roles.developer').joinpath('prompt.md').read_text(); "
            "assert HARNESS.checkout == Path.cwd(); assert '{{TICKET_JSON}}' in prompt; assert DEVELOPER.role == 'developer'",
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert resources.returncode == 0, resources.stderr
