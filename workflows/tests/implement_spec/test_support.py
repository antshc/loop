from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workflows.implement_spec import (
    DevResponse,
    DevResult,
    DevResultError,
    ExecutionStoreError,
    FileExecutionStore,
    PromptError,
    parse_dev_result,
    render_prompt,
)
from workflows.implement_spec.model.records import AgentCommitSubject

COMPLETED = {"commit": "abc", "summary": "done", "verification": "ran tests"}


def _envelope_dict(identifier: str = "Checkout|10", status: str = "completed", result: dict | None = None) -> dict:
    return {"identifier": identifier, "status": status, "result": COMPLETED if result is None else result}


def _envelope(identifier: str = "Checkout|10", status: str = "completed", result: dict | None = None) -> str:
    return json.dumps(_envelope_dict(identifier, status, result))


def test_render_prompt_substitutes_placeholders_and_leaves_other_braces_alone() -> None:
    assert render_prompt("A={{A}} B={{ B }} {A} ${A}", {"A": "1", "B": "2"}) == "A=1 B=2 {A} ${A}"


def test_render_prompt_never_expands_placeholders_arriving_through_arguments() -> None:
    assert render_prompt("{{BODY}}", {"BODY": "{{BODY}}"}) == "{{BODY}}"


def test_render_prompt_rejects_a_missing_argument_and_warns_on_an_unused_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(PromptError, match="missing prompt argument: X"):
        render_prompt("{{X}}", {})
    with caplog.at_level(logging.WARNING, logger="workflow.implement_spec"):
        assert render_prompt("plain", {"EXTRA": "1"}) == "plain"
    assert "unused prompt argument: EXTRA" in caplog.text


def test_parse_dev_result_decodes_a_completed_response() -> None:
    assert parse_dev_result(_envelope_dict()) == DevResult(
        "Checkout|10", "completed", commit="abc", summary="done", verification="ran tests"
    )


def test_parse_dev_result_decodes_a_failed_response() -> None:
    result = parse_dev_result(_envelope_dict(status="failed", result={"reason": "stuck"}))

    assert (result.status, result.reason) == ("failed", "stuck")


@pytest.mark.parametrize(
    "envelope",
    [
        {"identifier": "x", "status": "weird", "result": {}},
        {"identifier": "x", "status": "completed", "result": {"commit": "a", "summary": "s"}},
        {"identifier": "x", "status": "failed", "result": {}},
    ],
)
def test_parse_dev_result_rejects_a_malformed_response(envelope: dict) -> None:
    with pytest.raises(DevResultError):
        parse_dev_result(envelope)


def test_dev_response_requires_a_response_for_the_asked_task() -> None:
    identifier = AgentCommitSubject("Checkout", 10)
    response_without_identifier = json.dumps({"status": "completed", "result": COMPLETED})

    result = DevResponse(identifier).decode(f"x {response_without_identifier}")
    assert (result.identifier, result.commit) == (str(identifier), "abc")
    assert DevResponse(identifier).decode(f"x {_envelope()}").commit == "abc"
    with pytest.raises(DevResultError, match="no response object"):
        DevResponse(identifier).decode("nothing")
    with pytest.raises(DevResultError, match="does not match task"):
        DevResponse(identifier).decode(_envelope("Checkout|11"))


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
