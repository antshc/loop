from __future__ import annotations

import os
import random
import shutil
import string
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "pre-push"
MARKER = "SCAN_TEST_MARKER"


@pytest.fixture(params=["fake", "real"])
def git_env(tmp_path: Path, request: pytest.FixtureRequest) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    if request.param == "real":
        if shutil.which("gitleaks", path=env["PATH"]) is None:
            pytest.skip("Gitleaks is not installed")
    else:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        scanner = bin_dir / "gitleaks"
        scanner.write_text(
            """#!/usr/bin/env python3
import shlex
import subprocess
import sys

options = next(arg.partition("=")[2] for arg in sys.argv if arg.startswith("--log-opts="))
sys.stdin.read()
result = subprocess.run(
    ["git", "log", "-p", "-U0", *shlex.split(options)],
    capture_output=True,
    text=True,
)
if result.returncode:
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
sys.exit(int(any(
    line.startswith("+") and "SCAN_TEST_MARKER" in line
    for line in result.stdout.splitlines()
)))
""",
            encoding="utf-8",
        )
        scanner.chmod(0o755)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def _git(
    cwd: Path, env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def _commit(repo: Path, env: dict[str, str], content: str, message: str) -> None:
    (repo / "data.txt").write_text(content, encoding="utf-8")
    _git(repo, env, "add", "data.txt")
    _git(repo, env, "commit", "-m", message)


def _secret() -> str:
    suffix = "".join(
        random.Random(7).choices(string.ascii_letters + string.digits, k=36)
    )
    return "gh" + "p_" + suffix


def _repo(tmp_path: Path, env: dict[str, str], initial_content: str) -> Path:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, env, "init", "--bare", "--initial-branch=main", str(remote))
    _git(tmp_path, env, "init", "--initial-branch=main", str(repo))
    _git(repo, env, "config", "user.name", "Hook Test")
    _git(repo, env, "config", "user.email", "hook-test@example.invalid")
    _git(repo, env, "remote", "add", "origin", str(remote))
    _commit(repo, env, initial_content, "initial")
    _git(repo, env, "push", "origin", "main")
    shutil.copy2(HOOK, repo / ".git" / "hooks" / "pre-push")
    return repo


def test_new_branch_excludes_published_history(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    repo = _repo(tmp_path, git_env, f"{MARKER}={_secret()}\n")
    _git(repo, git_env, "switch", "-c", "feature")
    _commit(repo, git_env, "clean change\n", "clean")

    result = _git(repo, git_env, "push", "origin", "feature", check=False)

    assert result.returncode == 0, result.stderr
    assert _git(
        repo, git_env, "ls-remote", "--refs", "origin", "refs/heads/feature"
    ).stdout


@pytest.mark.parametrize("existing_branch", [False, True])
def test_push_rejects_secrets_removed_in_a_later_commit(
    tmp_path: Path, git_env: dict[str, str], existing_branch: bool
) -> None:
    repo = _repo(tmp_path, git_env, "clean base\n")
    _git(repo, git_env, "switch", "-c", "feature")
    if existing_branch:
        _git(repo, git_env, "push", "origin", "feature")

    token = _secret()
    _commit(repo, git_env, f"{MARKER}={token}\n", "add synthetic secret")
    _commit(repo, git_env, "clean again\n", "remove synthetic secret")
    if not existing_branch:
        _git(repo, git_env, "update-ref", "refs/remotes/origin/stale", "HEAD")

    result = _git(repo, git_env, "push", "origin", "feature", check=False)
    remote_ref = _git(
        repo, git_env, "ls-remote", "--refs", "origin", "refs/heads/feature"
    ).stdout

    assert result.returncode != 0
    assert token not in result.stdout + result.stderr
    if existing_branch:
        assert remote_ref.startswith(
            _git(repo, git_env, "rev-parse", "main").stdout.strip()
        )
    else:
        assert not remote_ref


def test_push_scans_merge_conflict_resolution(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    repo = _repo(tmp_path, git_env, "base\n")
    _git(repo, git_env, "switch", "-c", "feature")
    _commit(repo, git_env, "feature\n", "feature")
    _git(repo, git_env, "switch", "main")
    _commit(repo, git_env, "main\n", "main")
    _git(repo, git_env, "switch", "feature")
    merge = _git(repo, git_env, "merge", "--no-ff", "--no-commit", "main", check=False)
    assert merge.returncode != 0
    _commit(repo, git_env, f"{MARKER}={_secret()}\n", "resolve conflict")

    result = _git(repo, git_env, "push", "origin", "feature", check=False)

    assert result.returncode != 0
    assert not _git(
        repo, git_env, "ls-remote", "--refs", "origin", "refs/heads/feature"
    ).stdout


def test_push_scans_every_ref_when_scanner_reads_stdin(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    repo = _repo(tmp_path, git_env, "clean base\n")
    _git(repo, git_env, "switch", "-c", "a-clean")
    _commit(repo, git_env, "clean change\n", "clean")
    _git(repo, git_env, "switch", "-c", "z-secret", "main")
    _commit(repo, git_env, f"{MARKER}={_secret()}\n", "secret")

    result = _git(
        repo,
        git_env,
        "push",
        "origin",
        "a-clean",
        "z-secret",
        check=False,
    )

    assert result.returncode != 0
    assert not _git(
        repo, git_env, "ls-remote", "--refs", "origin", "refs/heads/a-clean"
    ).stdout
    assert not _git(
        repo, git_env, "ls-remote", "--refs", "origin", "refs/heads/z-secret"
    ).stdout


def test_push_scans_when_the_remote_tip_is_not_available_locally(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    repo = _repo(tmp_path, git_env, "clean base\n")
    other = tmp_path / "other"
    _git(tmp_path, git_env, "clone", str(tmp_path / "remote.git"), str(other))
    _git(other, git_env, "config", "user.name", "Hook Test")
    _git(other, git_env, "config", "user.email", "hook-test@example.invalid")
    _commit(other, git_env, "remote change\n", "remote change")
    _git(other, git_env, "push", "origin", "main")
    _commit(repo, git_env, f"{MARKER}={_secret()}\n", "local change")

    result = _git(repo, git_env, "push", "--force", "origin", "main", check=False)

    assert result.returncode != 0
    assert _git(
        repo, git_env, "ls-remote", "--refs", "origin", "refs/heads/main"
    ).stdout.startswith(_git(other, git_env, "rev-parse", "HEAD").stdout.strip())


def test_remote_discovery_failure_scans_full_history(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    repo = _repo(tmp_path, git_env, f"{MARKER}={_secret()}\n")
    _commit(repo, git_env, "clean change\n", "clean")
    commit = _git(repo, git_env, "rev-parse", "HEAD").stdout.strip()

    result = subprocess.run(
        [str(HOOK), "origin", str(tmp_path / "missing.git")],
        cwd=repo,
        env=git_env,
        input=f"refs/heads/feature {commit} refs/heads/feature {'0' * 40}\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Could not list remote refs" in result.stderr


def test_missing_scanner_blocks_push_but_allows_deletion(tmp_path: Path) -> None:
    zeros = "0" * 40
    commit = "1" * 40
    env = {"PATH": str(tmp_path)}

    push = subprocess.run(
        [str(HOOK), "origin", "unused"],
        input=f"refs/heads/main {commit} refs/heads/main {zeros}\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    deletion = subprocess.run(
        [str(HOOK), "origin", "unused"],
        input=f"(delete) {zeros} refs/heads/main {commit}\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert push.returncode != 0
    assert "gitleaks" in push.stderr.lower()
    assert deletion.returncode == 0, deletion.stderr
