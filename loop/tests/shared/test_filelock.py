"""The cross-platform file lock excludes other handles until it is released."""

from __future__ import annotations

from pathlib import Path

import pytest
from loop.shared.filelock import lock_file, unlock_file


def test_a_held_lock_blocks_another_handle_until_released(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with path.open("a") as first, path.open("a") as second:
        lock_file(first, wait=False)
        with pytest.raises(BlockingIOError):
            lock_file(second, wait=False)
        unlock_file(first)
        lock_file(second, wait=False)
        unlock_file(second)


def test_closing_the_handle_releases_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with path.open("a") as first:
        lock_file(first)
    with path.open("a") as second:
        lock_file(second, wait=False)
        unlock_file(second)
