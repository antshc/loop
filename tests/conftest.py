from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit_file(directory: Path, name: str, content: str, message: str) -> None:
    (directory / name).write_text(content)
    git(directory, "add", "-A")
    git(directory, "commit", "-m", message)
