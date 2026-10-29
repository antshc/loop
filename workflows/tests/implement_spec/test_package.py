from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from workflows.implement_spec import HARNESS, PROMPT, load_prompt
from workflows.implement_spec.agents import prompts
from workflows.tests.implement_spec.doubles import ImplementSpecHarness

ROOT = Path(__file__).resolve().parents[3]


def test_harness_and_default_prompt_paths_survive_the_package_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert HARNESS.checkout == ROOT
    assert HARNESS.worktrees == ROOT / "workspace" / f"{ROOT.name}.worktrees"
    assert PROMPT == ROOT / "workflows/implement_spec/prompts/implement.md"
    assert load_prompt("implement") == PROMPT.read_text()
    assert "{{TICKET_JSON}}" in load_prompt("implement")


def test_missing_named_prompt_raises_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prompts, "PROMPTS", tmp_path)

    with pytest.raises(FileNotFoundError, match="missing.md"):
        load_prompt("missing")


def test_missing_default_prompt_is_logged_and_fails_before_opening_a_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prompts, "PROMPTS", tmp_path)
    harness = ImplementSpecHarness(tmp_path)

    assert harness.run(argv=["--log-dir", str(harness.log_dir), "--log-level", "DEBUG"]) == 1
    assert harness.git_service.opened == []
    assert harness.prompts == []
    assert "implement.md" in (harness.log_dir / "implement_spec.log").read_text()


def test_implement_spec_runs_with_its_resources_and_the_shared_package_but_no_sibling_workflow(tmp_path: Path) -> None:
    package = tmp_path / "workflows" / "implement_spec"
    shutil.copytree(ROOT / "workflows/implement_spec", package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "workflows/shared", package.parent / "shared", ignore=shutil.ignore_patterns("__pycache__"))
    (package.parent / "__init__.py").write_text("")
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}

    help_result = subprocess.run(
        [sys.executable, "-m", "workflows.implement_spec", "--help"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "--log-dir" in help_result.stdout and "--log-level" in help_result.stdout

    resources = subprocess.run(
        [
            sys.executable, "-c",
            "from pathlib import Path; from workflows.implement_spec import HARNESS, load_prompt; "
            "assert HARNESS.checkout == Path.cwd(); assert '{{TICKET_JSON}}' in load_prompt('implement')",
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert resources.returncode == 0, resources.stderr
