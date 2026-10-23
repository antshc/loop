from __future__ import annotations

from pathlib import Path

import pytest

from conftest import event, per_call_log, write_log
from fakeloop.copilot_session import read_session


def test_per_call_log_splits_cached_input_and_counts_one_premium_request(tmp_path: Path) -> None:
    write_log(tmp_path / "events.jsonl", per_call_log("s1"))

    record = read_session(tmp_path / "events.jsonl")

    assert (record.input_tokens, record.cache_read_tokens, record.cache_write_tokens) == (500, 2_100, 400)
    assert record.total_input_tokens == 3_000
    assert record.output_tokens == 100
    assert record.premium_requests == 1.0
    assert record.total_aiu == 2.0
    assert record.aiu_by_type["output"] == pytest.approx(0.1)
    assert (record.api_calls, record.context_window, record.finish_reason) == (2, 200_000, "stop")


def test_tools_carry_start_and_end_times(tmp_path: Path) -> None:
    write_log(tmp_path / "events.jsonl", per_call_log("s1"))

    (tool,) = read_session(tmp_path / "events.jsonl").tools

    assert (tool.name, tool.ok, tool.result, tool.seconds) == ("bash", True, "clean", 0.5)
    assert tool.arguments == {"command": "git status"}


def test_shutdown_summary_is_used_when_the_log_has_no_per_call_records(tmp_path: Path) -> None:
    usage = {"inputTokens": 1_000, "outputTokens": 30, "cacheReadTokens": 600, "cacheWriteTokens": 300,
             "reasoningTokens": 2}
    write_log(tmp_path / "events.jsonl", [
        event("session.start", "00.000", sessionId="s2", copilotVersion="1.0.92"),
        event("session.model_change", "00.050", newModel="claude-sonnet-5"),
        event("session.shutdown", "05.000", totalPremiumRequests=1, totalNanoAiu=9_000_000_000,
              currentTokens=24_371,
              modelMetrics={"claude-sonnet-5": {"requests": {"count": 2, "cost": 1}, "usage": usage}}),
    ])

    record = read_session(tmp_path / "events.jsonl")

    assert record.request_model == "claude-sonnet-5"
    assert (record.input_tokens, record.api_calls, record.premium_requests) == (100, 2, 1)
    assert (record.total_aiu, record.context_tokens, record.aiu_by_type) == (9.0, 24_371, {})
