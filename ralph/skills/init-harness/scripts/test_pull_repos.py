"""Harness pull-repos script behavior tests.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent / "pull-repos.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pull_repos", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pull_repos = _load_module()


def run_script(harness_root: Path, *names: str) -> subprocess.CompletedProcess[str]:
    # The script reads its sibling .harness.json.user via __file__, mirroring how
    # /init-harness copies it to .github/skills/harness/ — so run a copy placed there.
    install_dir = harness_root / ".github" / "skills" / "harness"
    installed_script = install_dir / "pull-repos.py"
    if not installed_script.exists():
        install_dir.mkdir(parents=True, exist_ok=True)
        installed_script.write_bytes(SCRIPT_PATH.read_bytes())
    return subprocess.run(
        [sys.executable, str(installed_script), *names],
        cwd=install_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def write_settings(harness_root: Path, repos: list[dict]) -> None:
    install_dir = harness_root / ".github" / "skills" / "harness"
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / ".harness.json.user").write_text(json.dumps({"repos": repos}))


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result


def make_origin_with_commit(tmp_path: Path, name: str, content: str = "v1") -> Path:
    origin = tmp_path / f"{name}-origin.git"
    git("init", "--bare", str(origin), cwd=tmp_path)
    seed = tmp_path / f"{name}-seed"
    git("clone", str(origin), str(seed), cwd=tmp_path)
    (seed / "a.txt").write_text(content)
    git("add", "-A", cwd=seed)
    git("-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", "seed", cwd=seed)
    git("push", "origin", "HEAD:main", cwd=seed)
    return origin


def push_commit(origin: Path, tmp_path: Path, name: str, content: str) -> None:
    pusher = tmp_path / f"{name}-pusher"
    git("clone", str(origin), str(pusher), cwd=tmp_path)
    git("checkout", "main", cwd=pusher)
    (pusher / "a.txt").write_text(content)
    git("add", "-A", cwd=pusher)
    git("-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", "update", cwd=pusher)
    git("push", "origin", "main", cwd=pusher)


def clone_checkout(origin: Path, harness_root: Path, name: str) -> Path:
    checkout = harness_root / "workspace" / name
    checkout.parent.mkdir(parents=True, exist_ok=True)
    git("clone", str(origin), str(checkout), cwd=harness_root)
    git("checkout", "main", cwd=checkout)
    return checkout


class TestPullCommand:
    """Feature: Pull Command"""

    def test_no_repos_configured_reports_and_succeeds(self, tmp_path: Path):
        # Scenario: Report "no repos configured" and succeed when the working set is empty
        write_settings(tmp_path, [])

        result = run_script(tmp_path)

        assert result.returncode == 0
        assert "no repos configured" in result.stdout

    def test_missing_settings_file_reports_and_succeeds(self, tmp_path: Path):
        # Scenario: Report "no repos configured" and succeed when settings are missing
        result = run_script(tmp_path)

        assert result.returncode == 0
        assert "no repos configured" in result.stdout

    def test_invalid_repository_field_is_a_config_error(self, tmp_path: Path):
        # Scenario: Malformed repos entry is a configuration error before any repository is touched
        write_settings(tmp_path, [{"repository": "not-owner-slash-name", "branch": "main"}])

        result = run_script(tmp_path)

        assert result.returncode == 1
        assert not (tmp_path / "workspace").exists()

    def test_duplicate_repository_names_are_a_config_error(self, tmp_path: Path):
        # Scenario: Pull names an unknown or duplicated repository → configuration error
        write_settings(tmp_path, [
            {"repository": "acme/demo", "branch": "main"},
            {"repository": "other/demo", "branch": "main"},
        ])

        result = run_script(tmp_path)

        assert result.returncode == 1
        assert not (tmp_path / "workspace").exists()

    def test_harness_origin_entry_is_a_config_error(self, tmp_path: Path):
        # Scenario: repos entry equal to the harness's own origin is a configuration error
        origin = make_origin_with_commit(tmp_path, "harness")
        git("init", cwd=tmp_path)
        git("remote", "add", "origin", str(origin), cwd=tmp_path)
        write_settings(tmp_path, [{"repository": pull_repos.normalize_repository(str(origin)), "branch": "main"}])

        result = run_script(tmp_path)

        assert result.returncode == 1
        assert "own origin" in result.stderr

    def test_unknown_name_argument_is_a_config_error(self, tmp_path: Path):
        # Scenario: Pull names an unknown repository on the command line → configuration error
        write_settings(tmp_path, [{"repository": "acme/demo", "branch": "main"}])

        result = run_script(tmp_path, "not-configured")

        assert result.returncode == 1

    def test_branch_missing_on_remote_is_skipped(self, tmp_path: Path):
        # Scenario: Configured branch missing on the remote → skipped and reported
        origin = make_origin_with_commit(tmp_path, "demo")
        clone_checkout(origin, tmp_path, "demo")
        write_settings(tmp_path, [{"repository": "acme/demo", "branch": "release/9.9", "access": "read"}])

        result = run_script(tmp_path)

        assert result.returncode == 1
        assert "demo: skipped" in result.stdout
        assert "missing on remote" in result.stdout

    def test_read_repository_is_force_reset_keeping_untracked_files(self, tmp_path: Path):
        # Scenario: `read` repository diverged or dirty → forced reset to the remote branch; untracked files kept
        origin = make_origin_with_commit(tmp_path, "demo", content="v1")
        checkout = clone_checkout(origin, tmp_path, "demo")
        (checkout / "a.txt").write_text("dirty-local-edit")
        (checkout / "untracked.txt").write_text("keep-me")
        push_commit(origin, tmp_path, "demo", content="v2")
        write_settings(tmp_path, [{"repository": "acme/demo", "branch": "main", "access": "read"}])

        result = run_script(tmp_path)

        assert result.returncode == 0
        assert "demo: updated" in result.stdout
        assert (checkout / "a.txt").read_text() == "v2"
        assert (checkout / "untracked.txt").read_text() == "keep-me"

    def test_write_repository_with_dirty_tracked_changes_fails(self, tmp_path: Path):
        # Scenario: `write` repository dirty → that repository fails naming the dirty files
        origin = make_origin_with_commit(tmp_path, "demo")
        checkout = clone_checkout(origin, tmp_path, "demo")
        (checkout / "a.txt").write_text("dirty-local-edit")
        write_settings(tmp_path, [{"repository": "acme/demo", "branch": "main", "access": "write"}])

        result = run_script(tmp_path)

        assert result.returncode == 1
        assert "demo: failed" in result.stdout
        assert "a.txt" in result.stdout

    def test_write_repository_fast_forwards_when_clean(self, tmp_path: Path):
        # Scenario: Switch a clean `write` repository to its configured branch and fast-forward it
        origin = make_origin_with_commit(tmp_path, "demo", content="v1")
        checkout = clone_checkout(origin, tmp_path, "demo")
        push_commit(origin, tmp_path, "demo", content="v2")
        write_settings(tmp_path, [{"repository": "acme/demo", "branch": "main", "access": "write"}])

        result = run_script(tmp_path)

        assert result.returncode == 0
        assert "demo: updated" in result.stdout
        assert (checkout / "a.txt").read_text() == "v2"

    def test_write_repository_diverged_is_skipped(self, tmp_path: Path):
        # Scenario: Skip and report a `write` repository whose local branch has diverged from the remote
        origin = make_origin_with_commit(tmp_path, "demo", content="v1")
        checkout = clone_checkout(origin, tmp_path, "demo")
        (checkout / "a.txt").write_text("local-only-commit")
        git("add", "-A", cwd=checkout)
        git("-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", "local", cwd=checkout)
        push_commit(origin, tmp_path, "demo", content="v2")
        write_settings(tmp_path, [{"repository": "acme/demo", "branch": "main", "access": "write"}])

        result = run_script(tmp_path)

        assert result.returncode == 1
        assert "demo: skipped" in result.stdout
        assert "diverged" in result.stdout
        assert (checkout / "a.txt").read_text() == "local-only-commit"


class TestCloneRepo:
    """Feature: Clone Missing Checkout"""

    def test_reports_skipped_when_no_clone_tool_available(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # Scenario: Missing checkout and no clone tool available → that repository skipped and reported
        monkeypatch.setattr(pull_repos, "clone_available", lambda: False)
        entry = pull_repos.RepoEntry(repository="acme/demo", branch="main", access="read")

        error = pull_repos.clone_repo(entry, tmp_path / "workspace" / "demo")

        assert error is not None
        assert "no clone tool available" in error

    def test_reports_failure_message_from_gh(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # Scenario: gh clone failure surfaces gh's error
        monkeypatch.setattr(pull_repos, "clone_available", lambda: True)
        failed = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="repository not found")
        monkeypatch.setattr(pull_repos.subprocess, "run", lambda *a, **k: failed)
        entry = pull_repos.RepoEntry(repository="acme/demo", branch="main", access="read")

        error = pull_repos.clone_repo(entry, tmp_path / "workspace" / "demo")

        assert error is not None
        assert "clone failed" in error
        assert "repository not found" in error
