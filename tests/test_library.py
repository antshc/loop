from __future__ import annotations

import logging
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import orb
import orb.testing
from conftest import commit_file, git
from orb import (
    CommandError,
    ExtractionError,
    GitClient,
    PromptError,
    PromptPreprocessor,
    extract_json,
    extract_tag,
    parallel_settled,
)

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "orb"
WORKFLOWS = ROOT / "workflows"
FAKES = {"FakeGhCli", "FakeGitClient", "FakeDocker", "FakeCopilotCli", "FakeAgentClient"}


def test_preprocessor_substitutes_placeholders_and_runs_template_commands() -> None:
    preprocessor = PromptPreprocessor(lambda command: f"ran:{command}\n")

    text = preprocessor.process("A=${{A}} B=${{ B }} {{A}}\n!`echo ${{A}}`\n", {"A": "1", "B": "2"})

    assert text == "A=1 B=2 {{A}}\nran:echo 1\n"


def test_preprocessor_never_runs_commands_arriving_through_arguments() -> None:
    ran: list[str] = []
    preprocessor = PromptPreprocessor(lambda command: ran.append(command) or "")

    text = preprocessor.process("${{BODY}}", {"BODY": "!`rm -rf /`"})

    assert ran == [] and text == "!`rm -rf /`"


def test_preprocessor_rejects_a_missing_argument_and_warns_on_an_unused_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    preprocessor = PromptPreprocessor(str)

    with pytest.raises(PromptError, match="missing prompt argument: X"):
        preprocessor.process("${{X}}", {})
    with caplog.at_level(logging.WARNING, logger="orb"):
        assert preprocessor.process("plain", {"EXTRA": "1"}) == "plain"
    assert "unused prompt argument: EXTRA" in caplog.text


def test_extract_tag_and_json() -> None:
    assert extract_tag("x <plan> {\"a\": 1} </plan> y", "plan") == '{"a": 1}'
    assert extract_json("<plan>[1, 2]</plan>", "plan") == [1, 2]
    with pytest.raises(ExtractionError):
        extract_json("nothing", "plan")
    with pytest.raises(ExtractionError):
        extract_json("<plan>{</plan>", "plan")


def test_parallel_settled_isolates_failures_and_keeps_order() -> None:
    def worker(n: int) -> int:
        if n == 2:
            raise ValueError("boom")
        return n * 10

    outcomes = parallel_settled([1, 2, 3], worker, max_parallel=2)

    assert [o.ok for o in outcomes] == [True, False, True]
    assert [o.value for o in outcomes if o.ok] == [10, 30]
    assert isinstance(outcomes[1].error, ValueError)
    with pytest.raises(ValueError):
        parallel_settled([1], worker, max_parallel=0)


def test_git_client_creates_branch_and_worktree_reports_commits_and_merges(repo: Path) -> None:
    client = GitClient(repo)

    client.create_branch("feature/x")
    client.create_branch("feature/x")
    path = client.create_worktree("feature/x")
    base = client.head(path)
    commit_file(path, "a.txt", "a", "add a")
    commits = client.commits_since(path, base)
    client.merge_into_host(path)
    client.remove_worktree(path)

    assert len(commits) == 1
    assert (repo / "a.txt").read_text() == "a"
    assert not path.exists()
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(repo, "branch", "--list", "feature/x").strip() == "feature/x"


def test_git_client_rejects_invalid_branch_names_and_unknown_worktrees(repo: Path, tmp_path: Path) -> None:
    client = GitClient(repo)

    with pytest.raises(CommandError):
        client.create_branch("--evil")
    with pytest.raises(CommandError):
        client.head(tmp_path)


def test_public_api_exposes_no_fake_and_testing_exposes_every_fake() -> None:
    assert not FAKES & set(orb.__all__)
    assert FAKES <= set(orb.testing.__all__)


def test_packaging_declares_no_scripts_and_ships_no_workflow_file() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    workflow_files = {p.name for p in WORKFLOWS.glob("*.py") if p.name != "__init__.py"}
    package_files = {p.name for p in SRC.rglob("*.py") if p.name != "__init__.py"}

    assert "scripts" not in config["project"]
    assert not workflow_files & package_files


def test_import_linter_contracts_pass() -> None:
    lint_imports = Path(sys.executable).parent / "lint-imports"
    result = subprocess.run(
        [str(lint_imports)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
