from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from software_factory.legacy_slices.implement_spec import ExecutionStoreError, FileExecutionStore
from software_factory.legacy_slices.implement_spec.model.records import AgentCommitSubject


def test_agent_commit_subject_formats_ticket_identifiers_and_commit_prefixes() -> None:
    identifier = AgentCommitSubject("Checkout", 10)

    assert str(identifier) == "Checkout|10"
    assert identifier.initiative_commit_prefix == "ccode(Checkout|"
    assert identifier.to_subject() == "ccode(Checkout|10): "


def _store(tmp_path: Path, day: int = 1) -> FileExecutionStore:
    return FileExecutionStore(tmp_path, clock=lambda: datetime(2026, 1, day, 12, 0, 0, tzinfo=timezone.utc))


def _fail(store: FileExecutionStore, key: str = "t1") -> None:
    store.record_failure(key, owner="o", repo="r", task_id="1", title="T", items=[1])


def test_the_store_counts_failures_per_key_and_resets_one_key(tmp_path: Path) -> None:
    store = _store(tmp_path)

    _fail(store)
    _fail(store)
    _fail(store, "t2")

    assert (store.failed_attempts("t1"), store.failed_attempts("t2")) == (2, 1)
    store.reset("t1")
    assert (store.failed_attempts("t1"), store.failed_attempts("t2")) == (0, 1)


def test_the_store_keeps_one_log_file_per_day(tmp_path: Path) -> None:
    _fail(_store(tmp_path, 1))

    assert _store(tmp_path, 2).failed_attempts("t1") == 0
    assert (tmp_path / "implement_spec-execution-log-2026-01-01.json").is_file()


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}'])
def test_the_store_rejects_a_corrupt_log(tmp_path: Path, content: str) -> None:
    (tmp_path / "implement_spec-execution-log-2026-01-01.json").write_text(content)

    with pytest.raises(ExecutionStoreError):
        _store(tmp_path).failed_attempts("t1")
