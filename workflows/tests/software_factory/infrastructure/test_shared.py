from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from loop.shared.process import CommandError, git_command
from workflows.software_factory.infrastructure.state import JsonFile, StateError, process_lock


def test_atomic_failure_preserves_previous_state(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.write({"version": 1, "value": "before"})
    with patch("workflows.software_factory.infrastructure.state.os.replace", side_effect=OSError("interrupted")):
        with pytest.raises(OSError):
            state.write({"version": 1, "value": "after"})
    assert state.read_versioned({})["value"] == "before"
    assert list(tmp_path.iterdir()) == [state.path]


def test_invalid_json_is_reported_and_not_reset(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.path.write_text("{")

    with pytest.raises(StateError, match="Cannot read state"):
        state.read_versioned({"version": 1})
    assert state.path.read_text() == "{"


def test_a_wrong_schema_version_is_rejected(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.write({"version": 2})

    with pytest.raises(StateError, match="Invalid state schema"):
        state.read_versioned({"version": 1})


def test_process_lock_excludes_a_duplicate_worker_and_releases(tmp_path) -> None:
    with process_lock(tmp_path / "worker.lock"):
        with pytest.raises(StateError):
            with process_lock(tmp_path / "worker.lock"):
                pytest.fail("duplicate worker acquired lock")
    with process_lock(tmp_path / "worker.lock"):
        pass



def test_a_git_command_that_hangs_fails_as_a_command_error_after_its_timeout(tmp_path, monkeypatch) -> None:
    """Given a git that never returns, when the runtime's Git command runs it with a short timeout, then it fails as a CommandError naming the timeout instead of blocking."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nsleep 30\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    started = time.monotonic()

    with pytest.raises(CommandError, match="timed out after 0.2s"):
        git_command(("commit", "-m", "x"), tmp_path, timeout_s=0.2)

    assert time.monotonic() - started < 10
