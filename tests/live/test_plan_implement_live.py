"""Live test: `plan_implement.main` against the real Copilot CLI (docs/concepts/ops-test-strategy.md).

Opt-in via `pytest -m live`; excluded from the default run and skipped when `copilot` is not on the PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import git, init_pushed_repo
from workflows import plan_implement

pytestmark = pytest.mark.live

# Cheapest model and effort, sufficient to prove real CLI flags and response handling end to end.
_MODEL = "gpt-5-mini"
_EFFORT = "low"


def _run_branch(repo: Path) -> str:
    """The one branch `plan_implement` created, distinct from the harness's `main`."""
    branches = [name for name in git(repo, "branch", "--format=%(refname:short)").splitlines() if name != "main"]
    assert len(branches) == 1, branches
    return branches[0]


def test_the_workflow_creates_hello_txt_through_the_real_copilot_cli(tmp_path: Path) -> None:
    """Given the task to create hello.txt, when run against the real Copilot CLI with the cheapest model and
    effort, then it exits 0 and the run branch contains hello.txt with content 'hi'."""
    if shutil.which("copilot") is None:
        pytest.skip("copilot is not on the PATH")

    harness_root = init_pushed_repo(tmp_path / "harness")

    code = plan_implement.main(
        ["create hello.txt containing hi"],
        harness_root=harness_root,
        plan_model=_MODEL,
        plan_effort=_EFFORT,
        implement_model=_MODEL,
        implement_effort=_EFFORT,
    )

    assert code == 0
    branch = _run_branch(harness_root)
    assert git(harness_root, "show", f"{branch}:hello.txt").strip() == "hi"
