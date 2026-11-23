"""Failed-build logs saved outside the worktree, so agents read them from a file instead of carrying them in the argv-capped prompt."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


def log_folder() -> Path:
    folder = Path(tempfile.gettempdir()) / "babysit-logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def log_path(slug: str, number: int, key: str) -> Path:
    """The file holding one failure's logs; `key` identifies the failed run or build."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{slug}-{number}-{key}").strip("-")
    return log_folder() / f"{name}.log"


def save_log(slug: str, number: int, key: str, text: str | bytes) -> str:
    path = log_path(slug, number, key)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return str(path)
