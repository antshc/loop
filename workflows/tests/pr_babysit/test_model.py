from __future__ import annotations

import pytest

from workflows.pr_babysit.model import Blocked, PrState, exclusive


def test_invalid_json_is_not_reset(tmp_path):
    state = PrState(tmp_path, "a/b", 1)
    state.path.parent.mkdir()
    state.path.write_text("{")
    with pytest.raises(Blocked, match="Cannot read state"):
        PrState(tmp_path, "a/b", 1)
    assert state.path.read_text() == "{"


def test_state_of_another_pr_is_rejected(tmp_path):
    PrState(tmp_path, "a/b", 1).save()
    path = tmp_path / "prs" / "a--b--1.json"
    path.write_text(path.read_text().replace('"number": 1', '"number": 2'))
    with pytest.raises(Blocked, match="identity mismatch"):
        PrState(tmp_path, "a/b", 1)


def test_exclusive_lock_blocks_duplicate_worker_and_releases(tmp_path):
    with exclusive(tmp_path / "worker.lock"):
        with pytest.raises(Blocked, match="Another process owns"):
            with exclusive(tmp_path / "worker.lock"):
                pytest.fail("duplicate worker acquired lock")
    with exclusive(tmp_path / "worker.lock"):
        pass
