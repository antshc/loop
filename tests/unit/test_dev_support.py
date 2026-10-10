from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workflows.dev.errors import ExecutionStoreError, PromptError
from workflows.dev.logging_config import configure_logging
from workflows.dev.prompting import render_prompt
from workflows.dev.result import DevResult, DevResultError, parse_dev_result, parse_response
from workflows.dev.store import FileExecutionStore
from workflows.platforms.agent_response import extract_response
from workflows.platforms.work_tracking import WorkIdentifier

COMPLETED = {"commit": "abc", "summary": "done", "verification": "ran tests"}


def _envelope(identifier: str = "Checkout|10", status: str = "completed", result: dict | None = None) -> str:
    return json.dumps({"identifier": identifier, "status": status, "result": COMPLETED if result is None else result})


def test_render_prompt_substitutes_placeholders_and_leaves_other_braces_alone() -> None:
    assert render_prompt("A={{A}} B={{ B }} {A} ${A}", {"A": "1", "B": "2"}) == "A=1 B=2 {A} ${A}"


def test_render_prompt_never_expands_placeholders_arriving_through_arguments() -> None:
    assert render_prompt("{{BODY}}", {"BODY": "{{BODY}}"}) == "{{BODY}}"


def test_render_prompt_rejects_a_missing_argument_and_warns_on_an_unused_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(PromptError, match="missing prompt argument: X"):
        render_prompt("{{X}}", {})
    with caplog.at_level(logging.WARNING, logger="workflow.dev"):
        assert render_prompt("plain", {"EXTRA": "1"}) == "plain"
    assert "unused prompt argument: EXTRA" in caplog.text


def test_extract_response_returns_the_last_object_carrying_a_status() -> None:
    text = 'noise {"status": "completed", "result": {}} {"other": 1} {"status": "failed", "result": {"reason": "r"}} tail'

    assert json.loads(extract_response(text) or "")["status"] == "failed"


@pytest.mark.parametrize("text", ["", "no json", '{"status": "completed"', '{"other": 1}', "[1]"])
def test_extract_response_returns_none_without_a_complete_status_object(text: str) -> None:
    assert extract_response(text) is None


def test_parse_dev_result_decodes_a_completed_response() -> None:
    assert parse_dev_result(_envelope()) == DevResult(
        "Checkout|10", "completed", commit="abc", summary="done", verification="ran tests"
    )


def test_parse_dev_result_decodes_a_failed_response() -> None:
    result = parse_dev_result(_envelope(status="failed", result={"reason": "stuck"}))

    assert (result.status, result.reason) == ("failed", "stuck")


@pytest.mark.parametrize(
    "response",
    [
        "{not json",
        "[1]",
        json.dumps({"identifier": "x", "status": "weird", "result": {}}),
        json.dumps({"identifier": "x", "status": "completed", "result": {"commit": "a", "summary": "s"}}),
        json.dumps({"identifier": "x", "status": "failed", "result": {}}),
    ],
)
def test_parse_dev_result_rejects_a_malformed_response(response: str) -> None:
    with pytest.raises(DevResultError):
        parse_dev_result(response)


def test_parse_response_requires_a_response_for_the_asked_task() -> None:
    identifier = WorkIdentifier("Checkout", 10)

    assert parse_response(f"x {_envelope()}", identifier).commit == "abc"
    with pytest.raises(DevResultError, match="no response object"):
        parse_response("nothing", identifier)
    with pytest.raises(DevResultError, match="does not match task"):
        parse_response(_envelope("Checkout|11"), identifier)


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
    assert (tmp_path / "dev-execution-log-2026-01-01.json").is_file()


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}'])
def test_the_store_rejects_a_corrupt_log(tmp_path: Path, content: str) -> None:
    (tmp_path / "dev-execution-log-2026-01-01.json").write_text(content)

    with pytest.raises(ExecutionStoreError):
        _store(tmp_path).failed_attempts("t1")


def test_configure_logging_writes_json_lines_at_the_given_level(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "dev.log"
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        configure_logging(log_file, "WARNING")
        logging.getLogger("x").info("hidden")
        logging.getLogger("x").warning("shown")
        for handler in root.handlers:
            handler.flush()
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    records = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert [record["message"] for record in records] == ["shown"]
