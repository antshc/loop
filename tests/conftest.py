from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from loop import CommandResult


class FakeRunner:
    """Records every command GitClient issues and returns a scripted result for it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path | None, float | None]] = []
        self.returncode_for: dict[object, int] = {}
        self.stdout_for: dict[object, str] = {}
        self.stderr_for: dict[object, str] = {}
        self.raise_for: dict[object, Exception] = {}

    def __call__(
        self,
        args: tuple[str, ...] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult:
        label = args if isinstance(args, str) else " ".join(args)
        self.calls.append((label, cwd, timeout_s))
        key = (label, cwd)
        if key in self.raise_for:
            raise self.raise_for[key]
        if label in self.raise_for:
            raise self.raise_for[label]
        returncode = self.returncode_for.get(key, self.returncode_for.get(label, 0))
        stdout = self.stdout_for.get(key, self.stdout_for.get(label, ""))
        stderr = self.stderr_for.get(key, self.stderr_for.get(label, ""))
        return CommandResult(returncode, stdout, stderr)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_file(directory: Path, name: str, content: str, message: str) -> None:
    (directory / name).write_text(content)
    git(directory, "add", "-A")
    git(directory, "commit", "-m", message)


def init_pushed_repo(path: Path) -> Path:
    """Real git repo at `path` with a committed `main` pushed to a bare `origin`."""
    remote = path.parent / f"{path.name}-remote.git"
    remote.mkdir(parents=True)
    git(remote, "init", "--bare", "-b", "main")
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    commit_file(path, "README.md", "hello\n", "initial")
    git(path, "remote", "add", "origin", str(remote))
    git(path, "push", "origin", "main")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    commit_file(path, "README.md", "hello\n", "initial")
    return path
