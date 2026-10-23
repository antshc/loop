from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator


class Blocked(RuntimeError):
    """A PR needs intervention before its maintenance can continue."""


def read_json(path: Path, default: dict) -> dict:
    """Read versioned JSON without treating corrupt state as an empty file."""
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exception:
        raise Blocked(f"Cannot read state {path}: {exception}") from exception
    if not isinstance(value, dict) or value.get("version") != 1:
        raise Blocked(f"Invalid state schema: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    """Durably replace a JSON file without exposing partial writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    """Hold an OS lock which is released automatically when a process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exception:
            raise Blocked(f"Another process owns {path}") from exception
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class PrState:
    """Persist one PR's pending work and publication checkpoints."""

    def __init__(self, root: Path, repo: str, number: int) -> None:
        self.path = root / "prs" / f"{repo.replace('/', '--')}--{number}.json"
        self.data = read_json(self.path, {
            "version": 1, "repo": repo, "number": number, "status": "queued",
            "items": {}, "retries": {}, "copilot": {}, "batch": None,
        })
        if self.data.get("repo") != repo or self.data.get("number") != number:
            raise Blocked(f"State identity mismatch: {self.path}")
        if (self.data.get("status") not in {"queued", "ignored", "running", "blocked", "completed", "interrupted"}
                or not all(isinstance(self.data.get(key), dict) for key in ("items", "retries", "copilot"))
                or (self.data.get("batch") is not None and not isinstance(self.data["batch"], dict))
                or any(not isinstance(item, dict) or item.get("status") not in {"pending", "handled", "superseded"}
                       for item in self.data["items"].values())):
            raise Blocked(f"Invalid PR state: {self.path}")

    def save(self) -> None:
        """Checkpoint the current transaction."""
        write_json(self.path, self.data)

    def status(self, status: str, **values) -> None:
        """Record execution outcome and recovery information."""
        self.data.update(status=status, **values)
        self.save()
