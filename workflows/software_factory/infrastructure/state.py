"""Durable JSON state files and cross-process locks."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class StateError(RuntimeError):
    """A state file is unreadable, has the wrong schema, or is locked by another process."""


class JsonFile:
    """A JSON document at one path, replaced atomically so a crash never leaves a partial write."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def read(self, default: Any) -> Any:
        """The decoded document, or `default` when the file does not exist."""
        if not self.path.exists():
            return default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exception:
            raise StateError(
                f"Cannot read state {self.path}: {exception}"
            ) from exception

    def read_versioned(self, default: dict) -> dict:
        """Like `read`, but the document must be an object with `version: 1`."""
        value = self.read(default)
        if not isinstance(value, dict) or value.get("version") != 1:
            raise StateError(f"Invalid state schema: {self.path}")
        return value

    def write(self, value: Any, *, sort_keys: bool = True) -> None:
        """Durably replaces the document."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, indent=2, sort_keys=sort_keys)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    """Holds an OS lock that is released automatically when the process dies; StateError when already held."""
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
            raise StateError(f"Another process owns {path}") from exception
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
