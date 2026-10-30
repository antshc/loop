from __future__ import annotations

from unittest.mock import patch

import pytest

from software_factory.shared.state import JsonFile, StateError, process_lock


def test_atomic_failure_preserves_previous_state(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.write({"version": 1, "value": "before"})
    with patch("software_factory.shared.state.os.replace", side_effect=OSError("interrupted")):
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

