from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from orb import ExecutionStoreError, FileExecutionStore


def _clock(iso: str):
    moment = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    return lambda: moment


def test_missing_file_starts_as_no_attempts(tmp_path: Path) -> None:
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))

    assert store.failed_attempts("https://github.com/o/r/issues/1") == 0
    assert not (tmp_path / "dev-execution-log-2026-01-01.json").exists()


def test_record_failure_round_trips_through_json(tmp_path: Path) -> None:
    key = "https://github.com/o/r/issues/1"
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T12:00:00"))

    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10, 11])

    path = tmp_path / "dev-execution-log-2026-01-01.json"
    assert path.read_text() == (
        '[{"owner":"o","repo":"r","type":"spec","task_id":"1","title":"Spec","task":'
        f'"{key}","count":1,"last_run":"2026-01-01T12:00:00Z","last_items":[10,11],"partials":{{}}}}]'
    )
    reloaded = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T12:00:00"))
    assert reloaded.failed_attempts(key) == 1


def test_record_failure_increments_consecutive_count(tmp_path: Path) -> None:
    key = "https://github.com/o/r/issues/1"
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))

    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10])
    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10])

    assert store.failed_attempts(key) == 2


def test_non_array_file_raises_before_any_spec_is_attempted(tmp_path: Path) -> None:
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))
    (tmp_path / "dev-execution-log-2026-01-01.json").write_text('{"not": "an array"}')

    with pytest.raises(ExecutionStoreError):
        store.failed_attempts("https://github.com/o/r/issues/1")


def test_daily_rollover_lets_a_capped_spec_be_attempted_again(tmp_path: Path) -> None:
    key = "https://github.com/o/r/issues/1"
    yesterday = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))
    for _ in range(3):
        yesterday.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10])
    assert yesterday.failed_attempts(key) == 3

    today = FileExecutionStore(tmp_path, clock=_clock("2026-01-02T00:00:00"))
    assert today.failed_attempts(key) == 0


def test_reset_removes_the_record(tmp_path: Path) -> None:
    key = "https://github.com/o/r/issues/1"
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))
    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10])

    store.reset(key)

    assert store.failed_attempts(key) == 0
    assert (tmp_path / "dev-execution-log-2026-01-01.json").read_text() == "[]"


def test_partials_increment_and_resolve_per_ticket(tmp_path: Path) -> None:
    key = "https://github.com/o/r/issues/1"
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))

    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10, 11], partial_tickets=[10])
    assert store.partial_count(key, 10) == 1

    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10, 11], partial_tickets=[10])
    assert store.partial_count(key, 10) == 2

    store.record_failure(
        key, owner="o", repo="r", task_id="1", title="Spec", items=[10, 11], resolved_tickets=[10]
    )
    assert store.partial_count(key, 10) == 0


def test_omitted_ticket_partial_count_is_untouched(tmp_path: Path) -> None:
    key = "https://github.com/o/r/issues/1"
    store = FileExecutionStore(tmp_path, clock=_clock("2026-01-01T00:00:00"))
    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10, 11], partial_tickets=[10])

    store.record_failure(key, owner="o", repo="r", task_id="1", title="Spec", items=[10, 11], partial_tickets=[11])

    assert store.partial_count(key, 10) == 1
    assert store.partial_count(key, 11) == 1
