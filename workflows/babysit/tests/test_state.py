from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from workflows.pr_maintenance.state import Blocked, PrState, process_lock, read_json, write_json


def test_atomic_failure_preserves_previous_state(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"version": 1, "value": "before"})
    with patch("workflows.pr_maintenance.state.os.replace", side_effect=OSError("interrupted")):
        with pytest.raises(OSError):
            write_json(path, {"version": 1, "value": "after"})
    assert read_json(path, {})["value"] == "before"
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_json_is_not_reset(tmp_path):
    state = PrState(tmp_path, "a/b", 1)
    state.path.parent.mkdir()
    state.path.write_text("{")
    with pytest.raises(Blocked, match="Cannot read state"):
        PrState(tmp_path, "a/b", 1)
    assert state.path.read_text() == "{"


def test_process_lock_excludes_duplicate_worker_and_releases(tmp_path):
    with process_lock(tmp_path / "worker.lock"):
        with pytest.raises(Blocked):
            with process_lock(tmp_path / "worker.lock"):
                pytest.fail("duplicate worker acquired lock")
    with process_lock(tmp_path / "worker.lock"):
        pass
